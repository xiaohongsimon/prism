import sqlite3
from unittest.mock import patch

from prism.db import init_db


def _mkconn():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    # Ensure there's at least one cluster and source signals can be referenced
    conn.execute(
        "INSERT INTO sources (source_key, type, handle) VALUES ('manual:ext', 'manual', 'ext')"
    )
    conn.execute(
        "INSERT INTO clusters (id, date, topic_label) VALUES (1, '2026-04-19', 'external')"
    )
    conn.commit()
    return conn


FAKE_EXTRACTION = {
    "url_canonical": "https://example.com/post",
    "author": "zarazhangrui",
    "content_type": "article",
    "topics": ["方法论", "个人成长"],
    "summary_zh": "一篇关于持续学习的方法论文章。",
    "source_hint": {"type": "x", "handle": "zarazhangrui", "display_name": "Zara"},
}


def test_consumer_processes_pending_feed_and_proposes_source(tmp_path, monkeypatch):
    from prism.pipeline.external_feed import run_external_feed_consumer

    # Point at an empty/nonexistent sources.yaml so hint isn't considered present
    monkeypatch.setenv("PRISM_SOURCES_YAML", str(tmp_path / "nonexistent.yaml"))

    conn = _mkconn()
    conn.execute(
        "INSERT INTO external_feeds (url, user_note) VALUES (?, ?)",
        ("https://example.com/post", "这个作者真不错"),
    )
    conn.commit()

    with patch("prism.pipeline.external_feed.call_llm_json", return_value=FAKE_EXTRACTION):
        n = run_external_feed_consumer(conn)
    assert n == 1

    processed = conn.execute(
        "SELECT processed, extracted_json FROM external_feeds"
    ).fetchone()
    assert processed[0] == 1
    assert "方法论" in processed[1]

    # A source proposal was created
    prop = conn.execute(
        "SELECT source_type, display_name, origin FROM source_proposals"
    ).fetchone()
    assert prop == ("x", "Zara", "external_feed")


def test_consumer_skips_if_source_already_exists(tmp_path, monkeypatch):
    from prism.pipeline.external_feed import run_external_feed_consumer

    sources_yaml = tmp_path / "sources.yaml"
    sources_yaml.write_text(
        "sources:\n  - type: x\n    handle: zarazhangrui\n    depth: thread\n"
    )
    monkeypatch.setenv("PRISM_SOURCES_YAML", str(sources_yaml))

    conn = _mkconn()
    conn.execute(
        "INSERT INTO external_feeds (url, user_note) VALUES "
        "('https://example.com/post', '')"
    )
    conn.commit()

    with patch("prism.pipeline.external_feed.call_llm_json", return_value=FAKE_EXTRACTION):
        run_external_feed_consumer(conn)

    # No duplicate proposal since source already in yaml
    count = conn.execute("SELECT COUNT(*) FROM source_proposals").fetchone()[0]
    assert count == 0
