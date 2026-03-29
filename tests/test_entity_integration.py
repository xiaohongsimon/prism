"""
End-to-end integration test for the Prism v2 Entity Core pipeline (Task 8).

Exercises the full path:
  migrate_yaml_to_db → insert signals → run_entity_link (with mocked LLM)
  → assert entity_profiles, entity_events, lifecycle scores.
"""

from __future__ import annotations

import sqlite3
import textwrap
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from prism.db import init_db
from prism.pipeline.entities import migrate_yaml_to_db
from prism.pipeline.entity_link import run_entity_link


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


@pytest.fixture
def seed_yaml(tmp_path: Path) -> Path:
    """Write a minimal entities.yaml with vLLM, SGLang (project) and OpenAI (org)."""
    content = textwrap.dedent("""\
        project:
          - vLLM
          - SGLang
        org:
          - OpenAI
    """)
    p = tmp_path / "entities.yaml"
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_cluster(conn: sqlite3.Connection, *, date_str: str,
                    topic_label: str) -> int:
    cur = conn.execute(
        "INSERT INTO clusters (date, topic_label, item_count, merged_context) "
        "VALUES (?, ?, 1, '')",
        (date_str, topic_label),
    )
    conn.commit()
    return cur.lastrowid


def _insert_signal(conn: sqlite3.Connection, *, cluster_id: int, summary: str,
                   tags: list[str], signal_strength: int = 4) -> int:
    import json
    cur = conn.execute(
        "INSERT INTO signals "
        "(cluster_id, summary, why_it_matters, tags_json, signal_layer, "
        " signal_strength, is_current) "
        "VALUES (?, ?, '', ?, 'trend', ?, 1)",
        (cluster_id, summary, json.dumps(tags), signal_strength),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Mock LLM side_effect
# ---------------------------------------------------------------------------

def _mock_llm(signal: dict, candidates, known_entities, dt, model):
    """Return different entity lists depending on which signal is being processed."""
    summary: str = signal.get("summary", "")

    if "vLLM" in summary or "vllm" in summary.lower():
        return {
            "entities": [
                {"name": "vLLM", "category": "project",
                 "specificity": 5, "confidence": 0.95},
            ]
        }

    if "OpenAI" in summary or "GPT-5" in summary:
        return {
            "entities": [
                {"name": "OpenAI", "category": "org",
                 "specificity": 5, "confidence": 0.95},
                {"name": "GPT-5", "category": "model",
                 "specificity": 5, "confidence": 0.9},
            ]
        }

    if "LangGraph" in summary or "langgraph" in summary.lower():
        return {
            "entities": [
                {"name": "LangGraph", "category": "project",
                 "specificity": 4, "confidence": 0.85},
            ]
        }

    return {"entities": []}


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

def test_entity_pipeline_end_to_end(conn, seed_yaml):
    """Full pipeline: YAML seed → signals → LLM link → lifecycle scores."""

    # ------------------------------------------------------------------
    # Step 1: Seed entities from YAML
    # ------------------------------------------------------------------
    created = migrate_yaml_to_db(conn, seed_yaml)
    assert created == 3, f"Expected 3 seed entities (vLLM, SGLang, OpenAI), got {created}"

    # ------------------------------------------------------------------
    # Step 2: Insert 3 clusters + signals for today
    # ------------------------------------------------------------------
    today = date.today().isoformat()

    c1 = _insert_cluster(conn, date_str=today, topic_label="vLLM Inference")
    s1 = _insert_signal(
        conn,
        cluster_id=c1,
        summary="vLLM 0.8 released with speculative decoding optimization",
        tags=["vllm", "inference"],
        signal_strength=4,
    )

    c2 = _insert_cluster(conn, date_str=today, topic_label="OpenAI GPT-5")
    s2 = _insert_signal(
        conn,
        cluster_id=c2,
        summary="OpenAI announces GPT-5 with improved reasoning",
        tags=["openai", "gpt-5"],
        signal_strength=5,
    )

    c3 = _insert_cluster(conn, date_str=today, topic_label="LangGraph v2")
    s3 = _insert_signal(
        conn,
        cluster_id=c3,
        summary="LangGraph launches v2 with better agent support",
        tags=["langgraph", "agents"],
        signal_strength=3,
    )

    # ------------------------------------------------------------------
    # Step 3 + 4: Run entity link with mocked LLM
    # ------------------------------------------------------------------
    with patch("prism.pipeline.entity_link.extract_entities_llm",
               side_effect=_mock_llm):
        stats = run_entity_link(conn, today)

    # ------------------------------------------------------------------
    # Step 5: Assertions
    # ------------------------------------------------------------------

    # All three signals must be processed
    assert stats["signals_processed"] == 3, (
        f"Expected 3 signals processed, got {stats['signals_processed']}"
    )

    # --- vLLM: seeded from YAML, should be linked (not created) ---
    vllm_profile = conn.execute(
        "SELECT * FROM entity_profiles WHERE canonical_name = 'vllm'"
    ).fetchone()
    assert vllm_profile is not None, "vLLM entity_profile missing"

    vllm_events = conn.execute(
        "SELECT * FROM entity_events WHERE entity_id = ?", (vllm_profile["id"],)
    ).fetchall()
    assert len(vllm_events) >= 1, "vLLM should have at least one entity_event"

    # --- OpenAI: seeded from YAML, should be linked ---
    openai_profile = conn.execute(
        "SELECT * FROM entity_profiles WHERE canonical_name = 'openai'"
    ).fetchone()
    assert openai_profile is not None, "OpenAI entity_profile missing"

    openai_events = conn.execute(
        "SELECT * FROM entity_events WHERE entity_id = ?", (openai_profile["id"],)
    ).fetchall()
    assert len(openai_events) >= 1, "OpenAI should have at least one entity_event"

    # --- GPT-5: new entity, high confidence + specificity → created ---
    gpt5_profile = conn.execute(
        "SELECT * FROM entity_profiles WHERE canonical_name = 'gpt-5'"
    ).fetchone()
    assert gpt5_profile is not None, "GPT-5 entity_profile should have been created"

    # --- LangGraph: new entity, meets promotable threshold (conf=0.85 ≥ 0.8,
    #     spec=4 ≥ 4) → created directly ---
    langgraph_profile = conn.execute(
        "SELECT * FROM entity_profiles WHERE canonical_name = 'langgraph'"
    ).fetchone()
    assert langgraph_profile is not None, (
        "LangGraph entity_profile should have been created (meets promotable threshold)"
    )

    # --- SGLang: seeded from YAML but no signals mention it → no events ---
    sglang_profile = conn.execute(
        "SELECT * FROM entity_profiles WHERE canonical_name = 'sglang'"
    ).fetchone()
    assert sglang_profile is not None, "SGLang should exist from YAML migration"

    sglang_events = conn.execute(
        "SELECT * FROM entity_events WHERE entity_id = ?", (sglang_profile["id"],)
    ).fetchall()
    assert len(sglang_events) == 0, "SGLang should have no events (not mentioned)"

    # --- Lifecycle scores: entities with events should have m7_score > 0 ---
    for name, profile in [
        ("vLLM", vllm_profile),
        ("OpenAI", openai_profile),
        ("GPT-5", gpt5_profile),
        ("LangGraph", langgraph_profile),
    ]:
        refreshed = conn.execute(
            "SELECT * FROM entity_profiles WHERE id = ?", (profile["id"],)
        ).fetchone()
        assert refreshed["m7_score"] > 0, (
            f"{name} m7_score should be > 0 after lifecycle update, "
            f"got {refreshed['m7_score']}"
        )
        assert refreshed["event_count_7d"] >= 1, (
            f"{name} event_count_7d should be >= 1, got {refreshed['event_count_7d']}"
        )

    # --- SGLang lifecycle scores should be 0 ---
    sglang_refreshed = conn.execute(
        "SELECT * FROM entity_profiles WHERE id = ?", (sglang_profile["id"],)
    ).fetchone()
    assert sglang_refreshed["m7_score"] == 0.0, (
        f"SGLang m7_score should be 0.0, got {sglang_refreshed['m7_score']}"
    )
    assert sglang_refreshed["event_count_7d"] == 0, (
        f"SGLang event_count_7d should be 0, got {sglang_refreshed['event_count_7d']}"
    )
