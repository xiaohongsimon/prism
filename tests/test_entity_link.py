"""Tests for prism.pipeline.entity_link — Task 5."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from prism.db import init_db
from prism.pipeline.entity_link import (
    CANDIDATE_PROMOTE_THRESHOLD,
    expire_candidates,
    promote_ready_candidates,
    run_entity_link,
    stage_candidate,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conn():
    """In-memory SQLite with full Prism schema."""
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_cluster(conn: sqlite3.Connection, *, date: str = "2026-03-29",
                    topic_label: str = "vLLM Inference") -> int:
    cur = conn.execute(
        """
        INSERT INTO clusters (date, topic_label, item_count, merged_context)
        VALUES (?, ?, 1, 'test context')
        """,
        (date, topic_label),
    )
    conn.commit()
    return cur.lastrowid


def _insert_signal(conn: sqlite3.Connection, *, cluster_id: int,
                   summary: str = "vLLM released v0.5",
                   why_it_matters: str = "Faster inference for LLMs",
                   tags_json: str = '["vLLM", "inference"]',
                   signal_layer: str = "trend",
                   signal_strength: int = 4,
                   is_current: int = 1) -> int:
    cur = conn.execute(
        """
        INSERT INTO signals
            (cluster_id, summary, why_it_matters, tags_json,
             signal_layer, signal_strength, is_current)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (cluster_id, summary, why_it_matters, tags_json,
         signal_layer, signal_strength, is_current),
    )
    conn.commit()
    return cur.lastrowid


def _insert_entity(conn: sqlite3.Connection, *, canonical_name: str,
                   display_name: str, category: str = "project") -> int:
    cur = conn.execute(
        """
        INSERT INTO entity_profiles
            (canonical_name, display_name, category, first_seen_at)
        VALUES (?, ?, ?, datetime('now'))
        """,
        (canonical_name, display_name, category),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# test_stage_candidate
# ---------------------------------------------------------------------------

def test_stage_candidate_creates_new(conn):
    """Staging once should create a candidate with mention_count=1."""
    stage_candidate(conn, name_norm="vllm", display_name="vLLM",
                    category="project", signal_id=1)

    row = conn.execute(
        "SELECT * FROM entity_candidates WHERE name_norm = 'vllm'"
    ).fetchone()
    assert row is not None
    assert row["mention_count"] == 1
    assert row["display_name"] == "vLLM"


def test_stage_candidate_increments_existing(conn):
    """Staging the same candidate twice should produce mention_count=2."""
    stage_candidate(conn, name_norm="vllm", display_name="vLLM",
                    category="project", signal_id=1)
    stage_candidate(conn, name_norm="vllm", display_name="vLLM",
                    category="project", signal_id=2)

    row = conn.execute(
        "SELECT * FROM entity_candidates WHERE name_norm = 'vllm'"
    ).fetchone()
    assert row is not None
    assert row["mention_count"] == 2


def test_stage_candidate_sample_signals_max_3(conn):
    """sample_signals_json should hold at most 3 entries."""
    for sig_id in range(1, 6):
        stage_candidate(conn, name_norm="vllm", display_name="vLLM",
                        category="project", signal_id=sig_id)

    row = conn.execute(
        "SELECT sample_signals_json FROM entity_candidates WHERE name_norm = 'vllm'"
    ).fetchone()
    samples = json.loads(row["sample_signals_json"])
    assert len(samples) <= 3


# ---------------------------------------------------------------------------
# test_promote_ready_candidates
# ---------------------------------------------------------------------------

def test_promote_ready_candidates_basic(conn):
    """A candidate with mention_count >= threshold should be promoted."""
    # Insert directly with enough mentions
    conn.execute(
        """
        INSERT INTO entity_candidates
            (name_norm, display_name, category, mention_count,
             sample_signals_json, expires_at)
        VALUES ('sglang', 'SGLang', 'project', ?, '[]', datetime('now', '+30 days'))
        """,
        (CANDIDATE_PROMOTE_THRESHOLD,),
    )
    conn.commit()

    promoted = promote_ready_candidates(conn)
    assert promoted == 1

    # Should now be in entity_profiles
    profile = conn.execute(
        "SELECT * FROM entity_profiles WHERE canonical_name = 'sglang'"
    ).fetchone()
    assert profile is not None
    assert profile["display_name"] == "SGLang"
    assert profile["category"] == "project"

    # Should be removed from candidates
    cand = conn.execute(
        "SELECT * FROM entity_candidates WHERE name_norm = 'sglang'"
    ).fetchone()
    assert cand is None


def test_promote_ready_candidates_below_threshold(conn):
    """Candidates below threshold must not be promoted."""
    conn.execute(
        """
        INSERT INTO entity_candidates
            (name_norm, display_name, category, mention_count,
             sample_signals_json, expires_at)
        VALUES ('notyet', 'NotYet', 'project', ?, '[]', datetime('now', '+30 days'))
        """,
        (CANDIDATE_PROMOTE_THRESHOLD - 1,),
    )
    conn.commit()

    promoted = promote_ready_candidates(conn)
    assert promoted == 0

    cand = conn.execute(
        "SELECT * FROM entity_candidates WHERE name_norm = 'notyet'"
    ).fetchone()
    assert cand is not None


def test_promote_skips_existing_profile(conn):
    """If canonical_name already exists in entity_profiles, promotion is skipped."""
    # Pre-create profile
    _insert_entity(conn, canonical_name="sglang", display_name="SGLang")

    conn.execute(
        """
        INSERT INTO entity_candidates
            (name_norm, display_name, category, mention_count,
             sample_signals_json, expires_at)
        VALUES ('sglang', 'SGLang', 'project', ?, '[]', datetime('now', '+30 days'))
        """,
        (CANDIDATE_PROMOTE_THRESHOLD,),
    )
    conn.commit()

    promoted = promote_ready_candidates(conn)
    assert promoted == 0  # skipped because profile already exists


# ---------------------------------------------------------------------------
# test_expire_candidates
# ---------------------------------------------------------------------------

def test_expire_candidates_removes_expired(conn):
    """Candidates past expires_at should be deleted."""
    # Insert expired
    conn.execute(
        """
        INSERT INTO entity_candidates
            (name_norm, display_name, category, mention_count,
             sample_signals_json, expires_at)
        VALUES ('oldcand', 'OldCand', 'project', 1, '[]', datetime('now', '-1 day'))
        """
    )
    # Insert valid
    conn.execute(
        """
        INSERT INTO entity_candidates
            (name_norm, display_name, category, mention_count,
             sample_signals_json, expires_at)
        VALUES ('newcand', 'NewCand', 'project', 1, '[]', datetime('now', '+30 days'))
        """
    )
    conn.commit()

    deleted = expire_candidates(conn)
    assert deleted == 1

    assert conn.execute(
        "SELECT * FROM entity_candidates WHERE name_norm = 'oldcand'"
    ).fetchone() is None
    assert conn.execute(
        "SELECT * FROM entity_candidates WHERE name_norm = 'newcand'"
    ).fetchone() is not None


# ---------------------------------------------------------------------------
# test_run_entity_link_with_mock_llm
# ---------------------------------------------------------------------------

def test_run_entity_link_with_mock_llm(conn):
    """End-to-end run_entity_link with mocked LLM.

    Verifies:
    - Signals are processed.
    - LLM result drives entity creation when a new entity is returned with
      high confidence + sufficient specificity.
    - Linked entities produce entity_events.
    - Stats dict contains expected keys.
    """
    dt = "2026-03-29"
    cluster_id = _insert_cluster(conn, date=dt, topic_label="vLLM Inference")
    signal_id = _insert_signal(
        conn,
        cluster_id=cluster_id,
        summary="vLLM v0.5 achieves 2x throughput on H100.",
        why_it_matters="Critical for production LLM serving.",
        tags_json='["vLLM", "H100", "inference"]',
        signal_strength=4,
    )

    # Pre-insert a known entity that the LLM will "link"
    known_id = _insert_entity(conn, canonical_name="openai", display_name="OpenAI",
                              category="org")
    from prism.pipeline.entity_normalize import upsert_alias
    upsert_alias(conn, known_id, "OpenAI")

    mock_entities = {
        "entities": [
            # Will be linked to existing "openai" profile
            {"name": "OpenAI", "category": "org", "confidence": 0.95, "specificity": 6},
            # New entity: high confidence + high specificity → create profile
            {"name": "vLLM", "category": "project", "confidence": 0.95, "specificity": 6},
        ]
    }

    with patch("prism.pipeline.entity_link.extract_entities_llm",
               return_value=mock_entities):
        stats = run_entity_link(conn, dt, model=None)

    assert stats["signals_processed"] == 1
    assert stats["entities_linked"] >= 1  # OpenAI matched existing

    # vLLM should have been created as a new entity profile
    vllm_profile = conn.execute(
        "SELECT * FROM entity_profiles WHERE canonical_name = 'vllm'"
    ).fetchone()
    assert vllm_profile is not None, "Expected vLLM entity profile to be created"

    # OpenAI should have an entity_event
    events = conn.execute(
        "SELECT * FROM entity_events WHERE entity_id = ?", (known_id,)
    ).fetchall()
    assert len(events) >= 1

    # All expected keys present in stats
    for key in ("signals_processed", "entities_linked", "entities_created",
                "entities_staged", "candidates_expired", "candidates_promoted",
                "lifecycle_updated", "status_changes"):
        assert key in stats, f"Missing stats key: {key}"


def test_run_entity_link_stages_low_confidence(conn):
    """Entities returned by LLM with low confidence should go to candidates."""
    dt = "2026-03-29"
    cluster_id = _insert_cluster(conn, date=dt)
    _insert_signal(conn, cluster_id=cluster_id, signal_strength=1)

    mock_entities = {
        "entities": [
            # Low confidence + low specificity → should be staged
            {"name": "SomeNewThing", "category": "project", "confidence": 0.5, "specificity": 2},
        ]
    }

    with patch("prism.pipeline.entity_link.extract_entities_llm",
               return_value=mock_entities):
        stats = run_entity_link(conn, dt, model=None)

    assert stats["entities_staged"] >= 1
    assert stats["entities_created"] == 0

    cand = conn.execute(
        "SELECT * FROM entity_candidates WHERE name_norm = 'somenewthing'"
    ).fetchone()
    assert cand is not None


def test_run_entity_link_llm_failure_is_handled(conn):
    """If LLM raises, the pipeline should not crash and should return valid stats."""
    dt = "2026-03-29"
    cluster_id = _insert_cluster(conn, date=dt)
    _insert_signal(conn, cluster_id=cluster_id)

    with patch("prism.pipeline.entity_link.extract_entities_llm",
               side_effect=RuntimeError("LLM unavailable")):
        stats = run_entity_link(conn, dt, model=None)

    assert stats["signals_processed"] == 1
    # No entities created or linked due to LLM failure
    assert stats["entities_linked"] == 0
    assert stats["entities_created"] == 0


def test_run_entity_link_no_signals(conn):
    """With no signals for the date, stats should all be zero (except lifecycle)."""
    dt = "2026-03-29"
    stats = run_entity_link(conn, dt, model=None)

    assert stats["signals_processed"] == 0
    assert stats["entities_linked"] == 0
    assert stats["entities_created"] == 0
