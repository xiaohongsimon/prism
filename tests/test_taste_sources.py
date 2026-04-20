import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from prism.api.app import create_app
from prism.db import init_db


def _setup(tmp_path, monkeypatch):
    from prism import config as prism_config

    db = tmp_path / "test.sqlite3"
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute(
        "INSERT INTO source_proposals (source_type, source_config_json, display_name, "
        "rationale, origin) VALUES "
        "('x', ?, 'Zara', 'growth methodology', 'persona')",
        (json.dumps({"type": "x", "handle": "zarazhangrui", "depth": "thread"}),),
    )
    conn.commit()

    sources_yaml = tmp_path / "sources.yaml"
    sources_yaml.write_text("sources:\n  - type: x\n    handle: existing\n    depth: thread\n")
    monkeypatch.setattr(prism_config.settings, "source_config", sources_yaml)

    app = create_app(conn=conn)
    return TestClient(app), conn, sources_yaml


def test_taste_sources_list_shows_pending(tmp_path, monkeypatch):
    c, _, _ = _setup(tmp_path, monkeypatch)
    r = c.get("/taste/sources")
    assert r.status_code == 200
    assert "Zara" in r.text
    assert "growth methodology" in r.text


def test_accept_updates_yaml_and_marks_accepted(tmp_path, monkeypatch):
    c, conn, yaml_path = _setup(tmp_path, monkeypatch)
    prop_id = conn.execute("SELECT id FROM source_proposals").fetchone()[0]

    r = c.post(f"/taste/sources/{prop_id}/accept")
    assert r.status_code == 200

    assert "zarazhangrui" in yaml_path.read_text(encoding="utf-8")
    status = conn.execute(
        "SELECT status FROM source_proposals WHERE id = ?", (prop_id,)
    ).fetchone()[0]
    assert status == "accepted"


def test_reject_marks_rejected_without_yaml_change(tmp_path, monkeypatch):
    c, conn, yaml_path = _setup(tmp_path, monkeypatch)
    prop_id = conn.execute("SELECT id FROM source_proposals").fetchone()[0]
    original = yaml_path.read_text(encoding="utf-8")

    r = c.post(f"/taste/sources/{prop_id}/reject")
    assert r.status_code == 200
    assert yaml_path.read_text(encoding="utf-8") == original
    status = conn.execute(
        "SELECT status FROM source_proposals WHERE id = ?", (prop_id,)
    ).fetchone()[0]
    assert status == "rejected"
