"""Smoke tests for EPUB export."""

import sqlite3
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

from prism.db import init_db


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()


def _recent_ts(hours_ago: int = 1) -> str:
    t = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def _seed_followed_items(db):
    """Two sources across two FOLLOW_SOURCE_TYPES with items in the window."""
    db.execute(
        "INSERT INTO sources (source_key, type, handle, config_yaml, enabled) "
        "VALUES ('x:karpathy', 'x', 'karpathy', 'display_name: Andrej', 1)"
    )
    db.execute(
        "INSERT INTO sources (source_key, type, handle, config_yaml, enabled) "
        "VALUES ('youtube:3b1b', 'youtube', '3b1b', 'display_name: 3Blue1Brown', 1)"
    )
    db.execute(
        "INSERT INTO raw_items (source_id, url, title, body, body_zh, author, "
        "published_at, created_at) "
        "VALUES (1, 'https://x.com/karpathy/1', 'On LLMs', 'original english', "
        "'中文译文', 'karpathy', ?, ?)",
        (_recent_ts(2), _recent_ts(2)),
    )
    db.execute(
        "INSERT INTO raw_items (source_id, url, title, body, author, "
        "published_at, created_at) "
        "VALUES (2, 'https://yt.com/abc', 'Linear Algebra Ep 1', "
        "'video transcript', '3Blue1Brown', ?, ?)",
        (_recent_ts(10), _recent_ts(10)),
    )
    db.commit()


def test_gather_items_buckets_by_section(db):
    from prism.pipeline.export import gather_items

    _seed_followed_items(db)
    sections = gather_items(db, days=7)

    assert len(sections["x"]) == 1
    assert sections["x"][0].display_name == "Andrej"
    assert len(sections["x"][0].items) == 1

    assert len(sections["youtube"]) == 1
    assert sections["youtube"][0].items[0].title == "Linear Algebra Ep 1"


def test_gather_items_skips_out_of_window(db):
    from prism.pipeline.export import gather_items

    db.execute(
        "INSERT INTO sources (source_key, type, handle, enabled) "
        "VALUES ('x:old', 'x', 'old', 1)"
    )
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    db.execute(
        "INSERT INTO raw_items (source_id, url, title, body, created_at) "
        "VALUES (1, 'https://x.com/old', 't', 'b', ?)",
        (old_ts,),
    )
    db.commit()

    sections = gather_items(db, days=7)
    assert sections["x"] == []


def test_gather_items_respects_prefers_body_zh(db):
    from prism.pipeline.export import gather_items

    _seed_followed_items(db)
    sections = gather_items(db, days=7)
    x_item = sections["x"][0].items[0]
    assert x_item.body == "中文译文"
    assert x_item.body_en == "original english"


def test_build_epub_returns_valid_epub_bytes(db):
    from ebooklib import epub

    from prism.pipeline.export import build_epub

    _seed_followed_items(db)
    data = build_epub(db, days=7)

    # Basic sanity: non-empty, recognized as a ZIP (EPUB is zip-based).
    assert isinstance(data, bytes)
    assert len(data) > 500
    assert data[:2] == b"PK"

    # Round-trip: ebooklib should be able to read it back.
    import tempfile
    from pathlib import Path as _P

    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tf:
        tf.write(data)
        path = _P(tf.name)
    try:
        book = epub.read_epub(str(path))
        title = book.get_metadata("DC", "title")
        assert title and "Prism" in title[0][0]
    finally:
        path.unlink(missing_ok=True)


def test_build_epub_with_empty_db_still_produces_output(db):
    """No followed sources → still yields a valid EPUB (just the intro page)."""
    from prism.pipeline.export import build_epub

    data = build_epub(db, days=7)
    assert data[:2] == b"PK"
    assert len(data) > 500


def test_build_epub_skips_disabled_sources(db):
    from prism.pipeline.export import gather_items

    db.execute(
        "INSERT INTO sources (source_key, type, handle, enabled) "
        "VALUES ('x:disabled', 'x', 'disabled', 0)"
    )
    db.execute(
        "INSERT INTO raw_items (source_id, url, title, body, created_at) "
        "VALUES (1, 'https://x.com/d', 't', 'b', ?)",
        (_recent_ts(1),),
    )
    db.commit()

    sections = gather_items(db, days=7)
    assert sections["x"] == []
