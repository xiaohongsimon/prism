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
