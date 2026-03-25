from fastapi.testclient import TestClient
from prism.api.app import create_app


def test_get_signals(db):
    # Seed data
    db.execute("INSERT INTO clusters (id, date, topic_label, item_count) VALUES (1, '2026-03-24', 'test', 1)")
    db.execute("INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, analysis_type, is_current) VALUES (1, 'summary', 'actionable', 4, 'daily', 1)")
    db.commit()
    app = create_app(db)
    client = TestClient(app)
    resp = client.get("/api/signals?layer=actionable")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["signal_layer"] == "actionable"


def test_get_trends(db):
    db.execute("INSERT INTO trends (topic_label, date, heat_score, delta_vs_yesterday, is_current) VALUES ('vLLM', '2026-03-24', 8.0, 3.0, 1)")
    db.commit()
    app = create_app(db)
    client = TestClient(app)
    resp = client.get("/api/trends")
    assert resp.status_code == 200


def test_get_briefing(db):
    db.execute("INSERT INTO briefings (date, html, markdown) VALUES ('2026-03-24', '<h1>Test</h1>', '# Test')")
    db.commit()
    app = create_app(db)
    client = TestClient(app)
    resp = client.get("/api/briefing?date=2026-03-24")
    assert resp.status_code == 200


def test_search(db):
    # Seed raw_items — FTS5 triggers auto-populate item_search
    db.execute("INSERT INTO sources (id, source_key, type, handle) VALUES (1, 'x:test', 'x', 'test')")
    db.execute("INSERT INTO raw_items (source_id, url, title, body) VALUES (1, 'https://x.com/1', 'vLLM release v0.5', 'Major vLLM performance improvements')")
    db.commit()
    app = create_app(db)
    client = TestClient(app)
    resp = client.get("/api/search?q=vLLM")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert "vLLM" in data[0]["title"]


def test_source_crud(db):
    app = create_app(db)
    client = TestClient(app)
    resp = client.post("/api/sources", json={"type": "x", "handle": "test_user"})
    assert resp.status_code in (200, 201)
