import sqlite3
from fastapi.testclient import TestClient

from prism.db import init_db
from prism.api.app import create_app
from prism.web.auth import COOKIE_NAME, create_admin, login


def _mkconn():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _authed_client(conn):
    """TestClient with a valid admin session cookie (anon-gate bypass)."""
    create_admin(conn, "tester", "pw")
    token = login(conn, "tester", "pw")
    client = TestClient(create_app(conn=conn))
    client.cookies.set(COOKIE_NAME, token)
    return client


def _seed(conn, n=3):
    import json
    conn.execute(
        "INSERT OR IGNORE INTO sources (id, source_key, type, handle) "
        "VALUES (1, 'x:demo', 'x', 'demo')"
    )
    for i in range(1, n + 1):
        conn.execute(
            "INSERT OR IGNORE INTO clusters (id, date, topic_label) "
            "VALUES (?, '2026-04-19', 'AI')",
            (i,),
        )
        conn.execute(
            "INSERT INTO signals (id, cluster_id, summary, signal_layer, signal_strength, "
            "tags_json, is_current, analysis_type) VALUES (?, ?, ?, 'actionable', 3, ?, 1, 'daily')",
            (i, i, f"summary-{i}", json.dumps(["ai"])),
        )
        conn.execute(
            "INSERT INTO raw_items (id, source_id, url, author, body) "
            "VALUES (?, 1, ?, 'demo', 't')",
            (i, f"https://x/{i}"),
        )
        conn.execute(
            "INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (?, ?)",
            (i, i),
        )
    conn.commit()


def test_feed_route_returns_200_and_renders_signals():
    conn = _mkconn()
    _seed(conn, 3)
    app = create_app(conn=conn)
    r = TestClient(app).get("/feed")
    assert r.status_code == 200
    # feed.html renders shell; /feed/more?offset=0 loads cards via HTMX.
    # So we assert shell present, then hit /feed/more for content check.
    more = TestClient(app).get("/feed/more?offset=0")
    assert more.status_code == 200
    assert "summary-1" in more.text or "summary-2" in more.text or "summary-3" in more.text


def test_feed_action_save_writes_event():
    conn = _mkconn()
    _seed(conn, 1)
    client = _authed_client(conn)
    r = client.post(
        "/feed/action",
        data={"signal_id": "1", "action": "save", "target_key": "", "response_time_ms": "0"},
    )
    assert r.status_code == 200
    n = conn.execute("SELECT COUNT(*) FROM feed_interactions WHERE action='save'").fetchone()[0]
    assert n == 1


def test_feed_more_pagination():
    conn = _mkconn()
    _seed(conn, 5)
    app = create_app(conn=conn)
    client = TestClient(app)
    r = client.get("/feed/more?offset=2")
    assert r.status_code == 200


def test_root_redirects_to_feed():
    conn = _mkconn()
    _seed(conn, 1)
    app = create_app(conn=conn)
    client = TestClient(app)
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "/feed" in r.headers["location"]
