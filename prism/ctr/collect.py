"""Incremental materializer for CTR training samples.

Every time the user saves a signal, we immediately turn that event into
one training group (1 positive + N skip-above negatives) and persist it
to `ctr_samples`. This removes the need to rebuild samples from scratch
before each training run, and makes it possible to train on a snapshot
that's always up-to-date with the latest save.

Entry points:
  - materialize_save_samples(conn, save_event_id) — per-save, idempotent
  - materialize_from_db(db_path, save_event_id) — background-task safe
    (opens its own connection)
  - backfill(conn)                           — replay every historical save
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class MaterializeResult:
    group_id: int
    positives: int
    negatives: int
    skipped_reason: str | None = None  # set when nothing was written


def materialize_save_samples(
    conn: sqlite3.Connection,
    save_event_id: int,
) -> MaterializeResult:
    """Write the (positive, skip-above negatives) rows for one save event.

    Idempotent: deletes any existing rows for this group_id before
    writing, so replays and race-retries land on the same final state.

    Returns a MaterializeResult describing what happened. Groups that
    end up with no negatives (e.g. the saved card was at rank 0, or we
    have no impression trace) are NOT written — they contribute nothing
    to a pairwise-ranking loss, and keeping the table clean makes
    debugging easier.
    """
    save = conn.execute(
        "SELECT id, signal_id, created_at FROM feed_interactions "
        "WHERE id = ? AND action = 'save'",
        (save_event_id,),
    ).fetchone()
    if not save:
        return MaterializeResult(save_event_id, 0, 0, "not_a_save_event")

    sid = save["signal_id"]
    saved_at = save["created_at"]

    impr = conn.execute(
        "SELECT session_id, rank_in_session, served_at, feed_score "
        "FROM feed_impressions "
        "WHERE signal_id = ? AND served_at <= ? "
        "ORDER BY served_at DESC LIMIT 1",
        (sid, saved_at),
    ).fetchone()
    if not impr:
        return MaterializeResult(save_event_id, 0, 0, "no_impression_trace")

    session_id = impr["session_id"]
    pos_rank = impr["rank_in_session"]

    # Gather skip-above peers — dedup by signal_id, keep earliest rank.
    peers = conn.execute(
        "SELECT signal_id, MIN(rank_in_session) AS r, "
        "       MIN(served_at) AS t, MAX(feed_score) AS sc "
        "FROM feed_impressions "
        "WHERE session_id = ? AND rank_in_session < ? "
        "GROUP BY signal_id",
        (session_id, pos_rank),
    ).fetchall()

    # Exclude other save signals from this group's negatives — they are
    # positives in their own groups.
    other_saved = {
        r["signal_id"] for r in conn.execute(
            "SELECT DISTINCT signal_id FROM feed_interactions WHERE action = 'save'"
        ).fetchall()
    }

    rows: list[tuple] = [
        (save_event_id, sid, 1, session_id, pos_rank,
         float(impr["feed_score"] or 0.0), impr["served_at"]),
    ]
    for p in peers:
        psid = p["signal_id"]
        if psid == sid:
            continue
        if psid in other_saved:
            continue
        rows.append((
            save_event_id, psid, 0, session_id, p["r"],
            float(p["sc"] or 0.0), p["t"],
        ))

    # No negatives → nothing worth storing.
    if len(rows) <= 1:
        return MaterializeResult(save_event_id, 0, 0, "no_skip_above_peers")

    # Idempotent replace: blow away any previous rows for this group.
    conn.execute("DELETE FROM ctr_samples WHERE group_id = ?", (save_event_id,))
    conn.executemany(
        "INSERT INTO ctr_samples "
        "(group_id, signal_id, label, session_id, rank_in_session, "
        " feed_score, served_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return MaterializeResult(
        save_event_id,
        positives=1,
        negatives=len(rows) - 1,
    )


def materialize_from_db(db_path: str, save_event_id: int) -> MaterializeResult:
    """Background-task helper: open a fresh connection and materialize.

    FastAPI BackgroundTasks runs after the HTTP response is sent but
    in the same process; the request-scoped connection is gone by then,
    so we take a new one bound to the same SQLite file. SQLite's WAL
    mode (already enabled in init_db) handles the concurrent reader/
    writer cleanly.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return materialize_save_samples(conn, save_event_id)
    finally:
        conn.close()


def backfill(conn: sqlite3.Connection) -> dict:
    """Replay every save ever recorded into ctr_samples.

    Useful when bringing this system online for the first time, or when
    the schema of ctr_samples changes. Existing rows are left alone for
    groups that still materialize to the same shape (UNIQUE constraint
    + DELETE inside the per-save path handles duplicates).
    """
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM feed_interactions WHERE action = 'save' ORDER BY id"
    ).fetchall()]

    total_pos = total_neg = written_groups = 0
    skipped: dict[str, int] = {}
    for save_id in ids:
        res = materialize_save_samples(conn, save_id)
        if res.skipped_reason:
            skipped[res.skipped_reason] = skipped.get(res.skipped_reason, 0) + 1
        else:
            written_groups += 1
            total_pos += res.positives
            total_neg += res.negatives
    return {
        "scanned": len(ids),
        "groups_written": written_groups,
        "positives": total_pos,
        "negatives": total_neg,
        "skipped": skipped,
    }
