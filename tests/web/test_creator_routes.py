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
    """Creator profiles are accessible via /creator/<key> (/?tab=follow removed in W2)."""
    _seed_creators(db)
    resp_yt = client.get("/creator/youtube:testchannel")
    assert resp_yt.status_code == 200
    assert "Test Channel" in resp_yt.text
    resp_x = client.get("/creator/x:karpathy")
    assert resp_x.status_code == 200
    assert "karpathy" in resp_x.text


def test_creator_profile_youtube(client, db):
    """Creator profile should show video list."""
    _seed_creators(db)
    resp = client.get("/creator/youtube:testchannel")
    assert resp.status_code == 200
    assert "Video 1" in resp.text
    assert "Video 2" in resp.text
    assert "Test Channel" in resp.text


def test_creator_profile_x(client, db):
    """Creator profile for X should show tweets."""
    _seed_creators(db)
    resp = client.get("/creator/x:karpathy")
    assert resp.status_code == 200
    assert "Tweet content here" in resp.text


def test_creator_profile_not_found(client, db):
    """Non-existent source should 404."""
    resp = client.get("/creator/youtube:nonexistent")
    assert resp.status_code == 404


def test_article_detail_page(client, db):
    """Article detail page should render structured content."""
    _seed_creators(db)
    # Insert an article for Video 1 (raw_item_id=1)
    db.execute(
        """INSERT INTO articles (raw_item_id, title, subtitle, structured_body, highlights_json, word_count, model_id)
           VALUES (1, 'Video 1', 'Summary of video', '## Section 1\nContent here\n\n## Section 2\nMore content',
                   '["Key quote 1"]', 200, 'omlx')"""
    )
    db.commit()

    article = db.execute("SELECT id FROM articles WHERE raw_item_id = 1").fetchone()
    resp = client.get(f"/article/{article['id']}")
    assert resp.status_code == 200
    assert "Video 1" in resp.text
    assert "Summary of video" in resp.text
    assert "Section 1" in resp.text
    assert "Content here" in resp.text


def test_article_not_found(client, db):
    """Non-existent article should 404."""
    resp = client.get("/article/99999")
    assert resp.status_code == 404
