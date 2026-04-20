"""Tests for the 4-bucket signal classifier (unseen/impressed/clicked/saved)."""
from __future__ import annotations

import sqlite3

from prism.db import init_db


def _mkconn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _seed_signal(conn, sid, stype="x", source_key="x:demo"):
    conn.execute(
        "INSERT OR IGNORE INTO sources (id, source_key, type, handle) "
        "VALUES (1, ?, ?, 'demo')",
        (source_key, stype),
    )
    conn.execute(
        "INSERT OR IGNORE INTO clusters (id, date, topic_label, item_count) "
        "VALUES (?, '2026-04-19', 'AI', 1)",
        (sid,),
    )
    # created_at = now so it lands inside the default 30-day window.
    conn.execute(
        "INSERT INTO signals (id, cluster_id, summary, signal_layer, "
        "signal_strength, is_current, analysis_type, created_at) "
        "VALUES (?, ?, ?, 'actionable', 3, 1, 'daily', datetime('now'))",
        (sid, sid, f"s{sid}"),
    )
    conn.execute(
        "INSERT INTO raw_items (id, source_id, url, author, body, raw_json) "
        "VALUES (?, 1, ?, 'alice', 't', '{}')",
        (sid, f"https://x/{sid}"),
    )
    conn.execute(
        "INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (?, ?)",
        (sid, sid),
    )


def _impression(conn, sid):
    conn.execute(
        "INSERT INTO feed_impressions "
        "(trace_id, session_id, signal_id, rank_in_trace, rank_in_session) "
        "VALUES (?, 's1', ?, 0, 0)",
        (f"tr-{sid}", sid),
    )


def _interaction(conn, sid, action):
    conn.execute(
        "INSERT INTO feed_interactions (signal_id, action) VALUES (?, ?)",
        (sid, action),
    )


def test_all_four_buckets():
    from prism.ctr.stats import classify

    conn = _mkconn()
    # 1: unseen (no impression)
    # 2: impressed only
    # 3: impressed + clicked
    # 4: impressed + saved (save dominates over click)
    for sid in range(1, 5):
        _seed_signal(conn, sid)
    _impression(conn, 2)
    _impression(conn, 3); _interaction(conn, 3, "click")
    _impression(conn, 4); _interaction(conn, 4, "click"); _interaction(conn, 4, "save")
    conn.commit()

    report = classify(conn, days=30)
    assert report.overall.unseen == 1
    assert report.overall.impressed == 1
    assert report.overall.clicked == 1
    assert report.overall.saved == 1
    assert report.overall.total == 4
    assert report.overall.shown == 3
    # CTR = (1 clicked + 1 saved) / 3 shown
    assert abs(report.overall.ctr - 2 / 3) < 1e-6
    # save_rate = 1/3
    assert abs(report.overall.save_rate - 1 / 3) < 1e-6


def test_save_dominates_click_bucket():
    """A signal with both click and save must be classified as saved."""
    from prism.ctr.stats import classify

    conn = _mkconn()
    _seed_signal(conn, 1)
    _impression(conn, 1)
    _interaction(conn, 1, "click")
    _interaction(conn, 1, "save")
    conn.commit()

    report = classify(conn, days=30)
    assert report.overall.saved == 1
    assert report.overall.clicked == 0


def test_breakdown_by_source_type():
    from prism.ctr.stats import classify

    conn = _mkconn()
    _seed_signal(conn, 1, stype="x", source_key="x:a")
    # Fresh source id for YouTube so both rows keep distinct stypes.
    conn.execute(
        "INSERT INTO sources (id, source_key, type, handle) "
        "VALUES (2, 'youtube:a', 'youtube', 'yt')"
    )
    conn.execute(
        "INSERT INTO clusters (id, date, topic_label, item_count) "
        "VALUES (2, '2026-04-19', 'AI', 1)"
    )
    conn.execute(
        "INSERT INTO signals (id, cluster_id, summary, signal_layer, "
        "signal_strength, is_current, analysis_type, created_at) "
        "VALUES (2, 2, 's2', 'actionable', 3, 1, 'daily', datetime('now'))"
    )
    conn.execute(
        "INSERT INTO raw_items (id, source_id, url, author, body, raw_json) "
        "VALUES (2, 2, 'https://yt/2', 'bob', 't', '{}')"
    )
    conn.execute(
        "INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (2, 2)"
    )
    _impression(conn, 1); _interaction(conn, 1, "save")
    _impression(conn, 2)  # impressed-only
    conn.commit()

    report = classify(conn, days=30)
    assert report.by_source_type["x"].saved == 1
    assert report.by_source_type["youtube"].impressed == 1


def test_old_signals_excluded_by_window():
    from prism.ctr.stats import classify

    conn = _mkconn()
    _seed_signal(conn, 1)
    # Backdate the signal 90 days.
    conn.execute(
        "UPDATE signals SET created_at = datetime('now', '-90 days') WHERE id = 1"
    )
    _impression(conn, 1)
    conn.commit()

    report = classify(conn, days=30)
    assert report.overall.total == 0
