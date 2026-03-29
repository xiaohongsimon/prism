"""Tests for YAML migration and DB-backed entity functions (Task 6)."""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest

from prism.db import init_db
from prism.pipeline.entities import (
    load_entities_from_db,
    migrate_yaml_to_db,
    tag_entities_from_db,
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


@pytest.fixture
def sample_yaml(tmp_path: Path) -> Path:
    """Write a small entities.yaml with project, org, and person entries."""
    content = textwrap.dedent("""\
        project:
          - vLLM
          - SGLang
          - LangChain
        org:
          - OpenAI
          - Anthropic
        person:
          - {handle: karpathy, name: Andrej Karpathy}
    """)
    p = tmp_path / "entities.yaml"
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# migrate_yaml_to_db
# ---------------------------------------------------------------------------

def test_migrate_yaml_creates_entities(conn, sample_yaml):
    """Migrating YAML should create one entity_profile per entry."""
    count = migrate_yaml_to_db(conn, sample_yaml)
    # 3 project + 2 org + 1 person = 6
    assert count == 6

    rows = conn.execute("SELECT * FROM entity_profiles").fetchall()
    assert len(rows) == 6


def test_migrate_yaml_sets_mature_status(conn, sample_yaml):
    """All migrated entities should have status='mature' and needs_review=0."""
    migrate_yaml_to_db(conn, sample_yaml)
    rows = conn.execute("SELECT status, needs_review FROM entity_profiles").fetchall()
    for row in rows:
        assert row["status"] == "mature"
        assert row["needs_review"] == 0


def test_migrate_yaml_creates_aliases(conn, sample_yaml):
    """Each migrated entity should have at least one alias."""
    migrate_yaml_to_db(conn, sample_yaml)
    aliases = conn.execute("SELECT * FROM entity_aliases").fetchall()
    # At minimum one alias per entity; person entries have two (name + handle)
    assert len(aliases) >= 6


def test_migrate_yaml_person_handle_alias(conn, sample_yaml):
    """Person entry with separate handle should get an alias for the handle."""
    migrate_yaml_to_db(conn, sample_yaml)
    alias = conn.execute(
        "SELECT * FROM entity_aliases WHERE alias_norm = ?", ("karpathy",)
    ).fetchone()
    assert alias is not None, "Expected 'karpathy' alias to be present"


def test_migrate_yaml_idempotent(conn, sample_yaml):
    """Running migration twice should return 0 on the second run."""
    first = migrate_yaml_to_db(conn, sample_yaml)
    assert first == 6

    second = migrate_yaml_to_db(conn, sample_yaml)
    assert second == 0

    # Row count unchanged
    rows = conn.execute("SELECT COUNT(*) FROM entity_profiles").fetchone()
    assert rows[0] == 6


# ---------------------------------------------------------------------------
# load_entities_from_db
# ---------------------------------------------------------------------------

def test_load_entities_from_db_format(conn, sample_yaml):
    """load_entities_from_db should return dict {category: [display_name,...]}."""
    migrate_yaml_to_db(conn, sample_yaml)
    result = load_entities_from_db(conn)

    assert isinstance(result, dict)
    assert "project" in result
    assert "org" in result
    assert "person" in result

    assert "vLLM" in result["project"]
    assert "OpenAI" in result["org"]
    assert "Andrej Karpathy" in result["person"]


def test_load_entities_from_db_compatible_with_legacy(conn, sample_yaml):
    """Result should be usable by the original tag_entities() function."""
    from prism.pipeline.entities import tag_entities

    migrate_yaml_to_db(conn, sample_yaml)
    entities = load_entities_from_db(conn)

    matched = tag_entities("OpenAI released GPT-5", "", entities)
    assert "OpenAI" in matched


# ---------------------------------------------------------------------------
# tag_entities_from_db
# ---------------------------------------------------------------------------

def test_tag_entities_from_db_finds_vllm(conn, sample_yaml):
    """tag_entities_from_db should find 'vLLM' mentioned in the text."""
    migrate_yaml_to_db(conn, sample_yaml)
    matched = tag_entities_from_db(conn, "vLLM achieves 2x speedup", "")
    assert "vLLM" in matched


def test_tag_entities_from_db_case_insensitive(conn, sample_yaml):
    """Matching should be case-insensitive via alias_norm."""
    migrate_yaml_to_db(conn, sample_yaml)
    matched = tag_entities_from_db(conn, "VLLM inference", "")
    assert "vLLM" in matched


def test_tag_entities_from_db_empty_when_no_match(conn, sample_yaml):
    """No entities should be matched when text contains nothing relevant."""
    migrate_yaml_to_db(conn, sample_yaml)
    matched = tag_entities_from_db(conn, "weather forecast sunny", "")
    assert len(matched) == 0


def test_tag_entities_from_db_returns_display_name(conn, sample_yaml):
    """Matched alias should map back to the proper display_name."""
    migrate_yaml_to_db(conn, sample_yaml)
    # 'karpathy' is an alias; display_name is 'Andrej Karpathy'
    matched = tag_entities_from_db(conn, "karpathy posted a new blog", "")
    assert "Andrej Karpathy" in matched
