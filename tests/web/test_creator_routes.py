"""Tests for creator list and profile routes."""

import sqlite3
import json
import pytest
from fastapi.testclient import TestClient
from prism.db import init_db


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def client(db):
    from prism.api.app import create_app
    app = create_app(conn=db)
    return TestClient(app)


def _seed_creators(db):
    """Insert YouTube + X sources with raw_items."""
    # YouTube creator
    db.execute(
        "INSERT INTO sources (source_key, type, handle, config_yaml, enabled) VALUES (?, ?, ?, ?, 1)",
        ("youtube:testchannel", "youtube", "testchannel",
         'display_name: "Test Channel"\nchannel_id: UCtest123'),
    )
    db.execute(
        "INSERT INTO raw_items (source_id, url, title, body, author, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (1, "https://youtube.com/watch?v=v1", "Video 1", "transcript", "Test Channel"),
    )
    db.execute(
        "INSERT INTO raw_items (source_id, url, title, body, author, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (1, "https://youtube.com/watch?v=v2", "Video 2", "transcript2", "Test Channel"),
    )

    # X creator
    db.execute(
        "INSERT INTO sources (source_key, type, handle, enabled) VALUES (?, ?, ?, 1)",
        ("x:karpathy", "x", "karpathy"),
    )
    db.execute(
        "INSERT INTO raw_items (source_id, url, title, body, author, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (2, "https://x.com/karpathy/status/1", "", "Tweet content here", "karpathy"),
    )
    db.commit()


def test_follow_tab_shows_creators(client, db):
    """Follow tab should show creator cards, not mixed feed."""
    _seed_creators(db)
    resp = client.get("/?tab=follow")
    assert resp.status_code == 200
    assert "Test Channel" in resp.text
    assert "karpathy" in resp.text
