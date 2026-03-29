import sqlite3
from prism.db import init_db


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_feedback_table_exists():
    conn = _fresh_db()
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "feedback" in tables
    assert "preference_weights" in tables


def test_insert_feedback():
    conn = _fresh_db()
    # Create minimal source → raw_item → cluster → signal chain
    conn.execute("INSERT INTO sources (source_key, type) VALUES ('test:s', 'test')")
    conn.execute("INSERT INTO raw_items (source_id, url) VALUES (1, 'http://a')")
    conn.execute("INSERT INTO clusters (date, topic_label, item_count) VALUES ('2026-03-29', 'test', 1)")
    conn.execute("INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength) VALUES (1, 'test', 'noise', 3)")
    conn.execute("INSERT INTO feedback (signal_id, action) VALUES (1, 'like')")
    conn.commit()
    row = conn.execute("SELECT * FROM feedback WHERE signal_id = 1").fetchone()
    assert row["action"] == "like"
    assert row["created_at"] is not None


def test_preference_weights_upsert():
    conn = _fresh_db()
    conn.execute(
        "INSERT INTO preference_weights (dimension, key, weight) VALUES ('source', 'karpathy', 1.0)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO preference_weights (dimension, key, weight, updated_at) "
        "VALUES ('source', 'karpathy', 2.0, strftime('%Y-%m-%dT%H:%M:%S', 'now'))"
    )
    conn.commit()
    row = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='source' AND key='karpathy'"
    ).fetchone()
    assert row["weight"] == 2.0


def test_feedback_action_check_constraint():
    conn = _fresh_db()
    conn.execute("INSERT INTO sources (source_key, type) VALUES ('test:s', 'test')")
    conn.execute("INSERT INTO raw_items (source_id, url) VALUES (1, 'http://a')")
    conn.execute("INSERT INTO clusters (date, topic_label, item_count) VALUES ('2026-03-29', 'test', 1)")
    conn.execute("INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength) VALUES (1, 'test', 'noise', 3)")
    conn.commit()
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO feedback (signal_id, action) VALUES (1, 'invalid')")
