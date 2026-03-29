"""Tests for entity system database schema (Task 1 — Entity Core v2)."""
import sqlite3
import pytest
from pathlib import Path

from prism.db import get_connection, init_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _insert_profile(conn: sqlite3.Connection, **kwargs) -> int:
    defaults = dict(
        canonical_name="test-entity",
        display_name="Test Entity",
        category="project",
        first_seen_at="2026-01-01T00:00:00",
    )
    defaults.update(kwargs)
    cursor = conn.execute(
        """INSERT INTO entity_profiles
               (canonical_name, display_name, category, first_seen_at)
           VALUES (:canonical_name, :display_name, :category, :first_seen_at)""",
        defaults,
    )
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Table existence
# ---------------------------------------------------------------------------

class TestTablesExist:
    def test_entity_tables_created_by_init_db(self, tmp_path):
        conn = _make_db(tmp_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','shadow') ORDER BY name")
        tables = {row["name"] for row in cursor.fetchall()}
        for expected in ("entity_profiles", "entity_aliases", "entity_candidates", "entity_events"):
            assert expected in tables, f"Missing table: {expected}"

    def test_entity_search_virtual_table_exists(self, tmp_path):
        conn = _make_db(tmp_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entity_search'")
        assert cursor.fetchone() is not None

    def test_get_connection_also_creates_entity_tables(self, tmp_path):
        conn = get_connection(tmp_path / "prism.db")
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='entity_profiles'"
        )
        assert cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# entity_profiles
# ---------------------------------------------------------------------------

class TestEntityProfiles:
    def test_insert_profile_all_fields(self, tmp_path):
        conn = _make_db(tmp_path)
        conn.execute(
            """INSERT INTO entity_profiles
                   (canonical_name, display_name, category, status, summary,
                    needs_review, first_seen_at, last_event_at,
                    event_count_7d, event_count_30d, event_count_total,
                    m7_score, m30_score, metadata_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("vllm", "vLLM", "project", "growing", "Fast LLM inference engine",
             0, "2026-01-01T00:00:00", "2026-03-01T00:00:00",
             5, 20, 100, 0.75, 0.9, '{"url": "https://github.com/vllm-project/vllm"}'),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM entity_profiles WHERE canonical_name='vllm'").fetchone()
        assert row["display_name"] == "vLLM"
        assert row["category"] == "project"
        assert row["status"] == "growing"
        assert row["event_count_total"] == 100
        assert abs(row["m30_score"] - 0.9) < 1e-9

    def test_defaults_status_emerging(self, tmp_path):
        conn = _make_db(tmp_path)
        _insert_profile(conn)
        row = conn.execute("SELECT * FROM entity_profiles WHERE canonical_name='test-entity'").fetchone()
        assert row["status"] == "emerging"

    def test_defaults_needs_review_1(self, tmp_path):
        conn = _make_db(tmp_path)
        _insert_profile(conn)
        row = conn.execute("SELECT * FROM entity_profiles WHERE canonical_name='test-entity'").fetchone()
        assert row["needs_review"] == 1

    def test_canonical_name_unique(self, tmp_path):
        conn = _make_db(tmp_path)
        _insert_profile(conn)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_profile(conn)  # duplicate canonical_name

    def test_category_check_constraint_valid(self, tmp_path):
        conn = _make_db(tmp_path)
        for cat in ("person", "org", "project", "model", "technique", "dataset"):
            conn.execute(
                "INSERT INTO entity_profiles (canonical_name, display_name, category, first_seen_at) VALUES (?,?,?,?)",
                (f"ent-{cat}", f"Entity {cat}", cat, "2026-01-01"),
            )
        conn.commit()  # should not raise

    def test_category_check_constraint_rejects_invalid(self, tmp_path):
        conn = _make_db(tmp_path)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO entity_profiles (canonical_name, display_name, category, first_seen_at) VALUES (?,?,?,?)",
                ("bad", "Bad", "paper", "2026-01-01"),
            )
            conn.commit()


# ---------------------------------------------------------------------------
# entity_aliases
# ---------------------------------------------------------------------------

class TestEntityAliases:
    def test_insert_alias_linked_to_profile(self, tmp_path):
        conn = _make_db(tmp_path)
        entity_id = _insert_profile(conn)
        conn.execute(
            "INSERT INTO entity_aliases (alias_norm, entity_id, surface_form) VALUES (?,?,?)",
            ("vllm", entity_id, "vLLM"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM entity_aliases WHERE alias_norm='vllm'"
        ).fetchone()
        assert row["entity_id"] == entity_id
        assert row["surface_form"] == "vLLM"

    def test_alias_default_source_is_llm(self, tmp_path):
        conn = _make_db(tmp_path)
        entity_id = _insert_profile(conn)
        conn.execute(
            "INSERT INTO entity_aliases (alias_norm, entity_id, surface_form) VALUES (?,?,?)",
            ("alias1", entity_id, "Alias One"),
        )
        conn.commit()
        row = conn.execute("SELECT source FROM entity_aliases WHERE alias_norm='alias1'").fetchone()
        assert row["source"] == "llm"

    def test_alias_primary_key_composite(self, tmp_path):
        conn = _make_db(tmp_path)
        entity_id = _insert_profile(conn)
        conn.execute(
            "INSERT INTO entity_aliases (alias_norm, entity_id, surface_form) VALUES (?,?,?)",
            ("dup", entity_id, "Dup"),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO entity_aliases (alias_norm, entity_id, surface_form) VALUES (?,?,?)",
                ("dup", entity_id, "Dup2"),
            )
            conn.commit()


# ---------------------------------------------------------------------------
# entity_candidates
# ---------------------------------------------------------------------------

class TestEntityCandidates:
    def test_insert_candidate_with_expires_at(self, tmp_path):
        conn = _make_db(tmp_path)
        conn.execute(
            """INSERT INTO entity_candidates
                   (name_norm, display_name, category, expires_at)
               VALUES (?,?,?,?)""",
            ("flash-attn", "FlashAttention", "technique", "2026-04-30T00:00:00"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM entity_candidates WHERE name_norm='flash-attn'"
        ).fetchone()
        assert row["display_name"] == "FlashAttention"
        assert row["expires_at"] == "2026-04-30T00:00:00"

    def test_candidate_defaults(self, tmp_path):
        conn = _make_db(tmp_path)
        conn.execute(
            "INSERT INTO entity_candidates (name_norm, expires_at) VALUES (?,?)",
            ("cand1", "2026-05-01"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM entity_candidates WHERE name_norm='cand1'").fetchone()
        assert row["mention_count"] == 1
        assert row["sample_signals_json"] == "[]"

    def test_candidate_primary_key(self, tmp_path):
        conn = _make_db(tmp_path)
        conn.execute(
            "INSERT INTO entity_candidates (name_norm, expires_at) VALUES (?,?)",
            ("uniq", "2026-05-01"),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO entity_candidates (name_norm, expires_at) VALUES (?,?)",
                ("uniq", "2026-05-02"),
            )
            conn.commit()


# ---------------------------------------------------------------------------
# entity_events
# ---------------------------------------------------------------------------

class TestEntityEvents:
    def test_insert_event_linked_to_profile(self, tmp_path):
        conn = _make_db(tmp_path)
        entity_id = _insert_profile(conn)
        conn.execute(
            """INSERT INTO entity_events
                   (entity_id, date, event_type)
               VALUES (?,?,?)""",
            (entity_id, "2026-03-29", "release"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM entity_events WHERE entity_id=?", (entity_id,)
        ).fetchone()
        assert row["event_type"] == "release"
        assert row["role"] == "subject"
        assert row["impact"] == "medium"
        assert abs(row["confidence"] - 0.8) < 1e-9

    def test_event_impact_check_constraint_valid(self, tmp_path):
        conn = _make_db(tmp_path)
        entity_id = _insert_profile(conn)
        for impact in ("high", "medium", "low"):
            conn.execute(
                "INSERT INTO entity_events (entity_id, date, event_type, impact) VALUES (?,?,?,?)",
                (entity_id, "2026-03-29", "mention", impact),
            )
        conn.commit()  # should not raise

    def test_event_impact_check_constraint_rejects_invalid(self, tmp_path):
        conn = _make_db(tmp_path)
        entity_id = _insert_profile(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO entity_events (entity_id, date, event_type, impact) VALUES (?,?,?,?)",
                (entity_id, "2026-03-29", "mention", "critical"),
            )
            conn.commit()

    def test_event_signal_id_nullable(self, tmp_path):
        conn = _make_db(tmp_path)
        entity_id = _insert_profile(conn)
        conn.execute(
            "INSERT INTO entity_events (entity_id, date, event_type, signal_id) VALUES (?,?,?,?)",
            (entity_id, "2026-03-29", "mention", None),
        )
        conn.commit()
        row = conn.execute("SELECT signal_id FROM entity_events WHERE entity_id=?", (entity_id,)).fetchone()
        assert row["signal_id"] is None


# ---------------------------------------------------------------------------
# FTS5 trigger: entity_search auto-populated on insert
# ---------------------------------------------------------------------------

class TestEntitySearchFTS:
    def test_fts_populated_after_profile_insert(self, tmp_path):
        conn = _make_db(tmp_path)
        conn.execute(
            """INSERT INTO entity_profiles
                   (canonical_name, display_name, category, summary, first_seen_at)
               VALUES (?,?,?,?,?)""",
            ("vllm", "vLLM", "project", "Fast inference engine for large language models", "2026-01-01"),
        )
        conn.commit()
        results = conn.execute(
            "SELECT * FROM entity_search WHERE entity_search MATCH 'inference'"
        ).fetchall()
        assert len(results) >= 1
