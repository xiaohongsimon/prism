"""Tests for video-to-article pipeline."""

import json
import sqlite3
import pytest
from unittest.mock import patch, MagicMock

from prism.db import init_db


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()


def _insert_youtube_item(db, source_key="youtube:test", body="transcript text", title="Test Video", url="https://youtube.com/watch?v=abc"):
    """Helper to insert a source + raw_item for testing."""
    db.execute("INSERT INTO sources (source_key, type) VALUES (?, 'youtube')", (source_key,))
    db.execute(
        "INSERT INTO raw_items (source_id, url, title, body, author) VALUES (1, ?, ?, ?, 'TestChannel')",
        (url, title, body),
    )
    db.commit()
    return 1  # raw_item_id


def test_find_eligible_items(db):
    """Should find YouTube items with body content and no existing article."""
    from prism.pipeline.articlize import find_eligible_items

    _insert_youtube_item(db, body="transcript " * 100)
    items = find_eligible_items(db)
    assert len(items) == 1
    assert items[0]["title"] == "Test Video"


def test_find_eligible_items_skips_empty_body(db):
    """Items with empty body should be skipped."""
    from prism.pipeline.articlize import find_eligible_items

    _insert_youtube_item(db, body="")
    items = find_eligible_items(db)
    assert len(items) == 0


def test_find_eligible_items_skips_existing_article(db):
    """Items that already have an article should be skipped."""
    from prism.pipeline.articlize import find_eligible_items

    _insert_youtube_item(db)
    db.execute("INSERT INTO articles (raw_item_id, title) VALUES (1, 'Existing')")
    db.commit()
    items = find_eligible_items(db)
    assert len(items) == 0


def test_find_eligible_items_skips_long_body(db):
    """Items with body > 6000 chars should be skipped (MVP limit)."""
    from prism.pipeline.articlize import find_eligible_items

    _insert_youtube_item(db, body="x" * 6001)
    items = find_eligible_items(db)
    assert len(items) == 0


def test_parse_llm_response_valid():
    """Valid JSON response should parse correctly."""
    from prism.pipeline.articlize import parse_llm_response

    raw = '{"subtitle": "Summary", "body": "## Section\\nContent", "highlights": ["quote1"]}'
    result = parse_llm_response(raw)
    assert result["subtitle"] == "Summary"
    assert "## Section" in result["body"]
    assert len(result["highlights"]) == 1


def test_parse_llm_response_wrapped_in_markdown():
    """JSON wrapped in ```json ... ``` should parse correctly."""
    from prism.pipeline.articlize import parse_llm_response

    raw = 'Here is the result:\n```json\n{"subtitle": "S", "body": "## A\\nB", "highlights": []}\n```'
    result = parse_llm_response(raw)
    assert result["subtitle"] == "S"


def test_parse_llm_response_invalid():
    """Invalid response should return None."""
    from prism.pipeline.articlize import parse_llm_response

    assert parse_llm_response("This is not JSON at all") is None
    assert parse_llm_response('{"subtitle": "S", "body": ""}') is None  # empty body


def test_save_article(db):
    """Should insert article into DB."""
    from prism.pipeline.articlize import save_article

    _insert_youtube_item(db)
    save_article(db, raw_item_id=1, title="Test Video", subtitle="Summary",
                 structured_body="## Section\nContent", highlights=["q1"], model_id="qwen3")

    row = db.execute("SELECT * FROM articles WHERE raw_item_id = 1").fetchone()
    assert row is not None
    assert row["subtitle"] == "Summary"
    assert row["word_count"] > 0
