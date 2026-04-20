"""Tests for incremental materializer + backfill."""
from __future__ import annotations

import sqlite3

from prism.db import init_db


def _mkconn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _seed_signal(conn, sid):
    conn.execute(
        "INSERT OR IGNORE INTO sources (id, source_key, type, handle) "
        "VALUES (1, 'x:demo', 'x', 'demo')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO clusters (id, date, topic_label, item_count) "
        "VALUES (?, '2026-04-19', 'AI', 1)",
        (sid,),
    )
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


def _impression(conn, *, sid, session_id, rank, served_at="2026-04-19T10:00:00"):
    conn.execute(
        "INSERT INTO feed_impressions "
        "(trace_id, session_id, signal_id, rank_in_trace, rank_in_session, served_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (f"tr-{session_id}-{rank}", session_id, sid, rank, rank, served_at),
    )


def _save(conn, sid, created_at):
    cur = conn.execute(
        "INSERT INTO feed_interactions (signal_id, action, created_at) "
        "VALUES (?, 'save', ?)",
        (sid, created_at),
    )
    return cur.lastrowid


def test_materialize_writes_positive_and_negatives():
    from prism.ctr.collect import materialize_save_samples

    conn = _mkconn()
    for sid in range(1, 5):
        _seed_signal(conn, sid)
    for rank, sid in enumerate([1, 2, 3, 4]):
        _impression(conn, sid=sid, session_id="s1", rank=rank)
    save_id = _save(conn, 4, "2026-04-19T10:01:00")
    conn.commit()

    res = materialize_save_samples(conn, save_id)
    assert res.positives == 1
    assert res.negatives == 3

    rows = conn.execute(
        "SELECT signal_id, label FROM ctr_samples WHERE group_id = ? "
        "ORDER BY signal_id", (save_id,),
    ).fetchall()
    labels = {(r["signal_id"], r["label"]) for r in rows}
    assert (4, 1) in labels
    assert (1, 0) in labels and (2, 0) in labels and (3, 0) in labels


def test_materialize_is_idempotent():
    """Running twice for the same save event must leave the same rows."""
    from prism.ctr.collect import materialize_save_samples

    conn = _mkconn()
    for sid in range(1, 4):
        _seed_signal(conn, sid)
    for rank, sid in enumerate([1, 2, 3]):
        _impression(conn, sid=sid, session_id="s1", rank=rank)
    save_id = _save(conn, 3, "2026-04-19T10:01:00")
    conn.commit()

    materialize_save_samples(conn, save_id)
    first = conn.execute("SELECT COUNT(*) FROM ctr_samples").fetchone()[0]
    materialize_save_samples(conn, save_id)
    second = conn.execute("SELECT COUNT(*) FROM ctr_samples").fetchone()[0]
    assert first == second == 3


def test_materialize_skips_when_no_impression():
    from prism.ctr.collect import materialize_save_samples

    conn = _mkconn()
    _seed_signal(conn, 1)
    save_id = _save(conn, 1, "2026-04-19T10:00:00")
    conn.commit()

    res = materialize_save_samples(conn, save_id)
    assert res.skipped_reason == "no_impression_trace"
    assert conn.execute("SELECT COUNT(*) FROM ctr_samples").fetchone()[0] == 0


def test_materialize_skips_when_no_skip_above():
    """Save at rank 0 → no negatives → nothing written."""
    from prism.ctr.collect import materialize_save_samples

    conn = _mkconn()
    _seed_signal(conn, 1)
    _impression(conn, sid=1, session_id="s1", rank=0)
    save_id = _save(conn, 1, "2026-04-19T10:01:00")
    conn.commit()

    res = materialize_save_samples(conn, save_id)
    assert res.skipped_reason == "no_skip_above_peers"


def test_backfill_replays_all_saves():
    from prism.ctr.collect import backfill

    conn = _mkconn()
    for sid in range(1, 5):
        _seed_signal(conn, sid)
    for rank, sid in enumerate([1, 2, 3, 4]):
        _impression(conn, sid=sid, session_id="s1", rank=rank)
    _save(conn, 2, "2026-04-19T10:01:00")
    _save(conn, 4, "2026-04-19T10:02:00")
    conn.commit()

    stats = backfill(conn)
    assert stats["scanned"] == 2
    assert stats["groups_written"] == 2
    # save@4 → 2 negatives (sid 1 and 3; sid 2 excluded as another save)
    # save@2 → 1 negative (sid 1)
    assert stats["positives"] == 2
    assert stats["negatives"] == 3


def test_build_samples_reads_materialized_table():
    """When ctr_samples is populated, build_samples() must use it."""
    from prism.ctr.collect import materialize_save_samples
    from prism.ctr.samples import build_samples

    conn = _mkconn()
    for sid in range(1, 4):
        _seed_signal(conn, sid)
    for rank, sid in enumerate([1, 2, 3]):
        _impression(conn, sid=sid, session_id="s1", rank=rank)
    save_id = _save(conn, 3, "2026-04-19T10:01:00")
    conn.commit()
    materialize_save_samples(conn, save_id)

    samples = build_samples(conn)
    assert {s.signal_id for s in samples if s.label == 1} == {3}
    assert {s.signal_id for s in samples if s.label == 0} == {1, 2}
    # All samples belong to the one materialized group.
    assert len({s.group_id for s in samples}) == 1
