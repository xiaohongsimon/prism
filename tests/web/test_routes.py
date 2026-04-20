import sqlite3
from fastapi.testclient import TestClient
from prism.db import init_db
from prism.api.app import create_app


def _test_client():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute("INSERT INTO sources (source_key, type, handle) VALUES ('x:karpathy', 'x', 'karpathy')")
    conn.execute("INSERT INTO raw_items (source_id, url, title, published_at) VALUES (1, 'http://a', 'A', '2026-03-29T06:00:00')")
    conn.execute("INSERT INTO clusters (date, topic_label, item_count) VALUES ('2026-03-29', 'GPT-5 Leak', 1)")
    conn.execute("INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (1, 1)")
    conn.execute("INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, tags_json, is_current) VALUES (1, 'GPT-5 benchmark', 'actionable', 5, '[\"gpt\"]', 1)")
    conn.execute("INSERT INTO trends (topic_label, date, heat_score, is_current) VALUES ('GPT-5 Leak', '2026-03-29', 5.0, 1)")
    conn.commit()
    app = create_app(conn=conn)
    return TestClient(app)


def test_index_returns_html():
    client = _test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Prism" in resp.text


def test_feedback_post():
    client = _test_client()
    resp = client.post("/feedback", data={"signal_id": "1", "action": "like"})
    assert resp.status_code == 200
    assert "liked" in resp.text


def test_channel_page():
    client = _test_client()
    resp = client.get("/channel/x:karpathy")
    assert resp.status_code == 200
    assert "x:karpathy" in resp.text


def test_channel_unfollow():
    client = _test_client()
    resp = client.post("/channel/x:karpathy/unfollow")
    assert resp.status_code == 200
    assert "关注" in resp.text


def test_channel_follow():
    client = _test_client()
    client.post("/channel/x:karpathy/unfollow")
    resp = client.post("/channel/x:karpathy/follow")
    assert resp.status_code == 200
    assert "取消关注" in resp.text


def test_static_css():
    client = _test_client()
    resp = client.get("/static/style.css")
    assert resp.status_code == 200
    assert "background" in resp.text
