import sqlite3

from prism.db import init_db


def test_feed_interactions_table_exists():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='feed_interactions'"
    ).fetchone()
    assert row is not None


def test_feed_interactions_accepts_insert():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO feed_interactions (signal_id, action, target_key) "
        "VALUES (1, 'save', '')"
    )
    conn.execute(
        "INSERT INTO feed_interactions (signal_id, action, target_key) "
        "VALUES (0, 'follow_author', 'karpathy')"
    )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM feed_interactions").fetchone()[0]
    assert n == 2


def test_feed_interactions_indexes_exist():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    idx = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='feed_interactions'"
    ).fetchall()}
    assert "idx_feed_interactions_signal" in idx
    assert "idx_feed_interactions_action_created" in idx
