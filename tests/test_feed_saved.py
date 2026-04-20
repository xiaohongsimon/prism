import sqlite3
from fastapi.testclient import TestClient

from prism.db import init_db
from prism.api.app import create_app
from prism.web.feed import record_feed_action


def test_saved_page_lists_saved_signals():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute(
        "INSERT INTO clusters (id, date, topic_label) VALUES (1,'2026-04-19','AI')"
    )
    conn.execute(
        "INSERT INTO signals (id, cluster_id, summary, signal_layer, signal_strength, "
        "tags_json, is_current, analysis_type) "
        "VALUES (1,1,'saved-this','actionable',3,'[]',1,'daily')"
    )
    conn.commit()
    record_feed_action(conn, signal_id=1, action="save")

    r = TestClient(create_app(conn=conn)).get("/feed/saved")
    assert r.status_code == 200
    assert "saved-this" in r.text
