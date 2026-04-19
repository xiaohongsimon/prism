import sqlite3
from unittest.mock import patch

from fastapi.testclient import TestClient

from prism.api.app import create_app
from prism.db import init_db


def _client():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    app = create_app(conn=conn)
    return TestClient(app), conn


def test_persona_get_shows_form():
    c, _ = _client()
    r = c.get("/persona")
    assert r.status_code == 200
    assert "persona" in r.text.lower() or "个人" in r.text or "你现在是谁" in r.text


def test_persona_post_saves_and_triggers_extraction():
    c, conn = _client()

    fake_llm = {
        "summary": "TL",
        "bias_weights": [{"dimension": "tag", "key": "方法论", "weight": 2.0}],
        "candidate_sources": [
            {"type": "x", "handle_or_url": "zarazhangrui",
             "display_name": "Zara", "rationale": "seed", "category": "growth"}
        ],
    }
    with patch("prism.persona.call_llm_json", return_value=fake_llm):
        r = c.post(
            "/persona",
            data={
                "role": "TL",
                "goals": ["积累方法论"],
                "active_learning": "产品设计",
                "seed_handles": "zarazhangrui\ndanshipper",
                "dislike": "LLM 流水账",
                "style": ["方法论思考"],
                "language": "都行",
                "length": "都可以",
                "free_text": "想做个会学的推荐",
            },
            follow_redirects=False,
        )

    assert r.status_code in (302, 303)
    assert r.headers["location"].startswith("/taste/sources")

    # Verify DB state (same conn as the app)
    snap = conn.execute(
        "SELECT id, extracted_summary FROM persona_snapshots WHERE is_active = 1"
    ).fetchone()
    assert snap is not None and snap["extracted_summary"] == "TL"
    w = conn.execute(
        "SELECT key, weight FROM preference_weights WHERE dimension = 'persona_bias'"
    ).fetchall()
    assert any(row["key"] == "tag/方法论" and row["weight"] == 2.0 for row in w)
    props = conn.execute(
        "SELECT origin, display_name FROM source_proposals"
    ).fetchall()
    assert len(props) == 1
    assert props[0]["origin"] == "persona"
    assert props[0]["display_name"] == "Zara"
