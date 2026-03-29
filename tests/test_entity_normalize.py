"""Tests for prism.pipeline.entity_normalize."""

from pathlib import Path

import pytest

from prism.db import get_connection
from prism.pipeline.entity_normalize import (
    _jaro_winkler,
    normalize,
    resolve,
    upsert_alias,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entity(conn, *, canonical_name: str, display_name: str, category: str) -> int:
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
# normalize — basic
# ---------------------------------------------------------------------------

def test_normalize_vllm():
    assert normalize("vLLM") == "vllm"


def test_normalize_strips_surrounding_whitespace():
    assert normalize("  OpenAI  ") == "openai"


def test_normalize_preserves_hyphen():
    assert normalize("GPT-4-turbo") == "gpt-4-turbo"


# ---------------------------------------------------------------------------
# normalize — unicode
# ---------------------------------------------------------------------------

def test_normalize_nfkc_ligature():
    # ﬁ (U+FB01) → fi via NFKC
    assert normalize("ﬁne-tuning") == "fine-tuning"


# ---------------------------------------------------------------------------
# normalize — punctuation stripping
# ---------------------------------------------------------------------------

def test_normalize_strips_trailing_exclamation():
    assert normalize("vLLM!") == "vllm"


def test_normalize_strips_parentheses():
    assert normalize("(PagedAttention)") == "pagedattention"


# ---------------------------------------------------------------------------
# normalize — whitespace collapse
# ---------------------------------------------------------------------------

def test_normalize_collapses_internal_whitespace():
    assert normalize("deep  seek") == "deep seek"


# ---------------------------------------------------------------------------
# resolve — exact alias match
# ---------------------------------------------------------------------------

def test_resolve_exact_same_category(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)

    entity_id = _make_entity(conn, canonical_name="vllm", display_name="vLLM", category="project")
    upsert_alias(conn, entity_id, "vLLM")

    row = resolve(conn, "vllm", "project")
    assert row is not None
    assert row["canonical_name"] == "vllm"


def test_resolve_exact_any_category_fallback(tmp_path):
    """An alias in category 'project' is found even when searching 'model'."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)

    entity_id = _make_entity(conn, canonical_name="vllm", display_name="vLLM", category="project")
    upsert_alias(conn, entity_id, "vLLM")

    row = resolve(conn, "vllm", "model")
    assert row is not None
    assert row["canonical_name"] == "vllm"


# ---------------------------------------------------------------------------
# resolve — no match
# ---------------------------------------------------------------------------

def test_resolve_no_match_returns_none(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)

    row = resolve(conn, "nonexistent", "project")
    assert row is None


# ---------------------------------------------------------------------------
# resolve — fuzzy match
# ---------------------------------------------------------------------------

def test_resolve_fuzzy_match(tmp_path):
    """'vllms' should fuzzy-match the alias 'vllm' at >= 0.9."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)

    entity_id = _make_entity(conn, canonical_name="vllm", display_name="vLLM", category="project")
    upsert_alias(conn, entity_id, "vllm")

    score = _jaro_winkler("vllms", "vllm")
    assert score >= 0.9, f"Expected jaro_winkler >= 0.9, got {score}"

    row = resolve(conn, "vllms", "project", fuzzy_threshold=0.9)
    assert row is not None
    assert row["canonical_name"] == "vllm"


# ---------------------------------------------------------------------------
# resolve — cross-category isolation
# ---------------------------------------------------------------------------

def test_resolve_rejects_cross_category_fuzzy(tmp_path):
    """Fuzzy matching must not cross category boundaries.

    'goo' is close enough to 'go' (jaro-winkler >= 0.9) to fuzzy-match if
    category were ignored — but since 'go' is only registered as 'org', a
    search for 'goo' in category 'technique' must return None.
    """
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)

    # "go" is registered as an org, with alias "go"
    entity_id = _make_entity(conn, canonical_name="go", display_name="Go", category="org")
    upsert_alias(conn, entity_id, "go")

    # Confirm jaro-winkler score is above threshold so fuzzy would fire if category were ignored
    score = _jaro_winkler("goo", "go")
    assert score >= 0.9, f"Pre-condition failed: score={score}"

    # Searching for "goo" as a technique should return None — fuzzy is category-restricted
    row = resolve(conn, "goo", "technique")
    assert row is None


# ---------------------------------------------------------------------------
# upsert_alias — idempotent
# ---------------------------------------------------------------------------

def test_upsert_alias_creates_alias(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)

    entity_id = _make_entity(conn, canonical_name="openai", display_name="OpenAI", category="org")
    upsert_alias(conn, entity_id, "OpenAI")

    row = conn.execute(
        "SELECT * FROM entity_aliases WHERE entity_id = ?", (entity_id,)
    ).fetchone()
    assert row is not None
    assert row["alias_norm"] == "openai"
    assert row["surface_form"] == "OpenAI"


def test_upsert_alias_idempotent(tmp_path):
    """Calling upsert_alias twice with the same args must not raise."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)

    entity_id = _make_entity(conn, canonical_name="openai", display_name="OpenAI", category="org")
    upsert_alias(conn, entity_id, "OpenAI")
    upsert_alias(conn, entity_id, "OpenAI")  # must not raise

    count = conn.execute(
        "SELECT COUNT(*) FROM entity_aliases WHERE entity_id = ?", (entity_id,)
    ).fetchone()[0]
    assert count == 1
