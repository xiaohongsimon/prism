import sqlite3
from unittest.mock import patch

from click.testing import CliRunner

from prism.cli import cli
from prism.db import init_db


def test_process_external_feeds_cli(tmp_path, monkeypatch):
    db = tmp_path / "test.sqlite3"
    conn = sqlite3.connect(db)
    init_db(conn)
    conn.execute(
        "INSERT INTO external_feeds (url) VALUES ('https://example.com/x')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("PRISM_DB_PATH", str(db))
    # Point yaml override at a nonexistent path so the consumer always proposes
    # (otherwise real config/sources.yaml might shadow the test).
    monkeypatch.setenv("PRISM_SOURCES_YAML", str(tmp_path / "nope.yaml"))

    with patch(
        "prism.pipeline.external_feed.call_llm_json",
        return_value={
            "url_canonical": "https://example.com/x",
            "author": "x",
            "content_type": "article",
            "topics": [],
            "summary_zh": "",
            "source_hint": {"type": "x", "handle": "x"},
        },
    ):
        r = CliRunner().invoke(cli, ["process-external-feeds"])
    assert r.exit_code == 0, r.output

    processed = sqlite3.connect(db).execute(
        "SELECT processed FROM external_feeds"
    ).fetchone()[0]
    assert processed == 1


def test_sources_prune_dry_run(tmp_path, monkeypatch):
    db = tmp_path / "test.sqlite3"
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  - type: hn\n    feed: best\n  - type: x\n    handle: karpathy\n"
    )
    monkeypatch.setenv("PRISM_DB_PATH", str(db))
    monkeypatch.setenv("PRISM_SOURCES_YAML", str(yaml_path))

    conn = sqlite3.connect(db)
    init_db(conn)
    conn.execute(
        "INSERT INTO preference_weights (dimension, key, weight) VALUES "
        "('source', 'hn:best', -12.0), ('source', 'x:karpathy', 3.5)"
    )
    conn.commit()
    conn.close()

    r = CliRunner().invoke(cli, ["sources", "prune", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "hn:best" in r.output
    assert "-12" in r.output
    # dry run should not modify yaml
    assert "feed: best" in yaml_path.read_text()
    assert "pruned" not in yaml_path.read_text()


def test_sources_prune_yes_applies(tmp_path, monkeypatch):
    db = tmp_path / "test.sqlite3"
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(
        "sources:\n  - type: hn\n    feed: best\n  - type: x\n    handle: karpathy\n"
    )
    monkeypatch.setenv("PRISM_DB_PATH", str(db))
    monkeypatch.setenv("PRISM_SOURCES_YAML", str(yaml_path))

    conn = sqlite3.connect(db)
    init_db(conn)
    conn.execute(
        "INSERT INTO preference_weights (dimension, key, weight) VALUES "
        "('source', 'hn:best', -12.0)"
    )
    conn.commit()
    conn.close()

    r = CliRunner().invoke(cli, ["sources", "prune", "--yes"])
    assert r.exit_code == 0, r.output
    text = yaml_path.read_text()
    assert "# pruned" in text
    assert "- type: x" in text and "handle: karpathy" in text
