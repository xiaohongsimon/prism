"""Tests for skip-above sample construction."""
from __future__ import annotations

import sqlite3

from prism.db import init_db


def _mkconn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _seed_signal(conn, sid, source_key="x:demo", stype="x"):
    conn.execute(
        "INSERT OR IGNORE INTO sources (id, source_key, type, handle) "
        "VALUES (1, ?, ?, 'demo')",
        (source_key, stype),
    )
    conn.execute(
        "INSERT OR IGNORE INTO clusters (id, date, topic_label) "
        "VALUES (?, '2026-04-19', 'AI')",
        (sid,),
    )
    conn.execute(
        "INSERT INTO signals (id, cluster_id, summary, signal_layer, "
        "signal_strength, is_current, analysis_type) "
        "VALUES (?, ?, ?, 'actionable', 3, 1, 'daily')",
        (sid, sid, f"s{sid}"),
    )
    conn.execute(
        "INSERT INTO raw_items (id, source_id, url, author, body) "
        "VALUES (?, 1, ?, ?, 't')",
        (sid, f"https://x/{sid}", f"a{sid}"),
    )
    conn.execute(
        "INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (?, ?)",
        (sid, sid),
    )


def _log_impression(conn, *, sid, session_id, rank, served_at, feed_score=0.0):
    conn.execute(
        "INSERT INTO feed_impressions "
        "(trace_id, session_id, signal_id, rank_in_trace, rank_in_session, "
        " feed_score, served_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (f"tr-{session_id}-{rank}", session_id, sid, rank, rank, feed_score, served_at),
    )


def _log_save(conn, *, sid, created_at):
    conn.execute(
        "INSERT INTO feed_interactions (signal_id, action, created_at) "
        "VALUES (?, 'save', ?)",
        (sid, created_at),
    )


def test_skip_above_basic_group():
    """Save on rank 3 → signals at ranks 0..2 are negatives."""
    from prism.ctr.samples import build_samples

    conn = _mkconn()
    for sid in range(1, 5):
        _seed_signal(conn, sid)
    conn.commit()

    for rank, sid in enumerate([1, 2, 3, 4]):
        _log_impression(
            conn, sid=sid, session_id="s1", rank=rank,
            served_at="2026-04-19T10:00:00",
        )
    _log_save(conn, sid=4, created_at="2026-04-19T10:01:00")
    conn.commit()

    samples = build_samples(conn)
    # 1 positive (sid=4) + 3 negatives (sid=1,2,3).
    labels = {(s.signal_id, s.label) for s in samples}
    assert (4, 1) in labels
    assert (1, 0) in labels
    assert (2, 0) in labels
    assert (3, 0) in labels
    # Single group.
    assert len({s.group_id for s in samples}) == 1


def test_skip_above_ignores_other_saves_as_negatives():
    """Signals saved in their OWN group must not appear as negatives elsewhere."""
    from prism.ctr.samples import build_samples

    conn = _mkconn()
    for sid in range(1, 5):
        _seed_signal(conn, sid)
    conn.commit()

    for rank, sid in enumerate([1, 2, 3, 4]):
        _log_impression(
            conn, sid=sid, session_id="s1", rank=rank,
            served_at="2026-04-19T10:00:00",
        )
    # Two saves: sid=2 and sid=4 — each is its own group.
    _log_save(conn, sid=2, created_at="2026-04-19T10:00:30")
    _log_save(conn, sid=4, created_at="2026-04-19T10:01:00")
    conn.commit()

    samples = build_samples(conn)
    # Group for sid=4 must not include sid=2 as a negative.
    g4 = [s for s in samples if s.label == 1 and s.signal_id == 4]
    assert g4
    g4_id = g4[0].group_id
    g4_neg = [s.signal_id for s in samples if s.group_id == g4_id and s.label == 0]
    assert 2 not in g4_neg


def test_save_without_impression_is_skipped():
    """A save with no matching impression contributes nothing."""
    from prism.ctr.samples import build_samples

    conn = _mkconn()
    _seed_signal(conn, 1)
    conn.commit()
    _log_save(conn, sid=1, created_at="2026-04-19T10:00:00")
    conn.commit()

    samples = build_samples(conn)
    assert samples == []


def test_groups_without_negatives_are_dropped():
    """A save at rank 0 (top card) has no skip-above — group is dropped."""
    from prism.ctr.samples import build_samples

    conn = _mkconn()
    _seed_signal(conn, 1)
    conn.commit()
    _log_impression(
        conn, sid=1, session_id="s1", rank=0,
        served_at="2026-04-19T10:00:00",
    )
    _log_save(conn, sid=1, created_at="2026-04-19T10:00:30")
    conn.commit()

    samples = build_samples(conn)
    assert samples == []
