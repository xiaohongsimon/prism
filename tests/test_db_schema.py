import sqlite3
from prism.db import init_db


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def test_persona_snapshots_table_exists():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    assert "persona_snapshots" in _tables(conn)
    cols = _columns(conn, "persona_snapshots")
    assert {"id", "answers_json", "free_text", "seed_handles_json",
            "extracted_summary", "is_active", "created_at"} <= cols


def test_source_proposals_table_exists():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    assert "source_proposals" in _tables(conn)
    cols = _columns(conn, "source_proposals")
    assert {"id", "source_type", "source_config_json", "display_name",
            "rationale", "origin", "origin_ref", "sample_preview_json",
            "status", "snooze_until", "created_at", "reviewed_at"} <= cols


def test_external_feeds_has_extracted_json_column():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    cols = _columns(conn, "external_feeds")
    assert "extracted_json" in cols
