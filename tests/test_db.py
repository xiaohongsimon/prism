import pytest
from prism.db import init_db, insert_source, get_source_by_key, insert_job_run, insert_raw_item


def test_init_db_creates_all_tables(db):
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = {row["name"] for row in cursor.fetchall()}
    expected = {"sources", "raw_items", "clusters", "cluster_items",
                "signals", "cross_links", "trends", "briefings", "job_runs",
                "item_search", "signal_search"}
    assert expected.issubset(tables)


def test_insert_and_get_source(db):
    insert_source(db, source_key="x:karpathy", type="x", handle="karpathy",
                  config_yaml="depth: thread", origin="yaml")
    src = get_source_by_key(db, "x:karpathy")
    assert src is not None
    assert src["handle"] == "karpathy"
    assert src["enabled"] == 1


def test_insert_job_run(db):
    job_id = insert_job_run(db, job_type="sync", status="ok", stats_json='{"sources": 10}')
    assert job_id > 0
    row = db.execute("SELECT * FROM job_runs WHERE id = ?", (job_id,)).fetchone()
    assert row["job_type"] == "sync"


def test_fts5_triggers_sync_on_insert(db):
    """FTS5 triggers should auto-populate search index when raw_items are inserted."""
    insert_source(db, source_key="x:test", type="x", handle="test")
    insert_raw_item(db, source_id=1, url="https://example.com/1",
                    title="vLLM inference optimization", body="New vLLM release speeds up inference")
    results = db.execute("SELECT * FROM item_search WHERE item_search MATCH 'vLLM'").fetchall()
    assert len(results) == 1


def test_articles_table_exists(db):
    """articles table should exist after init_db."""
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='articles'"
    ).fetchone()
    assert row is not None, "articles table not created"


def test_articles_insert_and_read(db):
    """Basic CRUD on articles table."""
    # Need a source and raw_item first
    db.execute(
        "INSERT INTO sources (source_key, type, handle) VALUES (?, ?, ?)",
        ("youtube:test", "youtube", "test"),
    )
    db.execute(
        "INSERT INTO raw_items (source_id, url, title, body, author) VALUES (?, ?, ?, ?, ?)",
        (1, "https://youtube.com/watch?v=abc", "Test Video", "transcript...", "TestChannel"),
    )
    db.commit()

    db.execute(
        """INSERT INTO articles (raw_item_id, title, subtitle, structured_body, highlights_json, word_count, model_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (1, "Test Video", "One line summary", "## Section\nContent", '["quote1"]', 100, "qwen3"),
    )
    db.commit()

    row = db.execute("SELECT * FROM articles WHERE raw_item_id = 1").fetchone()
    assert row["title"] == "Test Video"
    assert row["subtitle"] == "One line summary"
    assert row["word_count"] == 100


def test_articles_unique_raw_item_id(db):
    """raw_item_id should be unique — one article per raw_item."""
    import sqlite3 as _sqlite3
    db.execute("INSERT INTO sources (source_key, type) VALUES ('yt:t', 'youtube')")
    db.execute("INSERT INTO raw_items (source_id, url, title) VALUES (1, 'https://yt.com/1', 'V1')")
    db.commit()
    db.execute("INSERT INTO articles (raw_item_id, title) VALUES (1, 'Article 1')")
    db.commit()
    with pytest.raises(_sqlite3.IntegrityError):
        db.execute("INSERT INTO articles (raw_item_id, title) VALUES (1, 'Article 1 duplicate')")


def test_post_wave1_tables_exist():
    """After Wave 1 cleanup (2026-04-23), pairwise/BT tables are gone but
    the surviving auxiliary tables (decision_log, external_feeds) and
    UNIQUE constraint on external_feeds.url must stick around.
    """
    import sqlite3
    from prism.db import init_db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    for t in ["decision_log", "external_feeds"]:
        assert t in tables, f"Missing table: {t}"
    for t in ["pairwise_comparisons", "signal_scores", "source_weights",
              "ctr_samples", "feed_impressions"]:
        assert t not in tables, f"Dead Wave 1 table should be dropped: {t}"
    # external_feeds still enforces UNIQUE on url
    conn.execute("INSERT INTO external_feeds (url, topic) VALUES ('http://a', 'test')")
    try:
        conn.execute("INSERT INTO external_feeds (url, topic) VALUES ('http://a', 'dupe')")
        assert False, "Should have raised IntegrityError for duplicate URL"
    except sqlite3.IntegrityError:
        pass
