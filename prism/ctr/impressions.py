"""Impression logging — every signal served by /feed/more ends up here.

session_id groups consecutive scrolls that happen within SESSION_GAP_MIN
of each other. rank_in_session is a monotonic position across the entire
session, so skip-above sample construction has a stable ordering even
when the user pages through several /feed/more calls.
"""
from __future__ import annotations

import sqlite3
import uuid
from typing import Iterable

SESSION_GAP_MIN = 30


def _resolve_session(conn: sqlite3.Connection) -> tuple[str, int]:
    """Return (session_id, next rank_in_session) for the upcoming impressions.

    If the most recent impression happened within SESSION_GAP_MIN, reuse
    its session_id and continue the rank counter. Otherwise start fresh.
    """
    row = conn.execute(
        "SELECT session_id FROM feed_impressions "
        "WHERE served_at > datetime('now', ?) "
        "ORDER BY served_at DESC LIMIT 1",
        (f"-{SESSION_GAP_MIN} minutes",),
    ).fetchone()
    if not row:
        return str(uuid.uuid4()), 0
    session_id = row["session_id"] if isinstance(row, sqlite3.Row) else row[0]
    max_rank = conn.execute(
        "SELECT MAX(rank_in_session) FROM feed_impressions WHERE session_id = ?",
        (session_id,),
    ).fetchone()[0]
    return session_id, (max_rank + 1 if max_rank is not None else 0)


def log_impressions(
    conn: sqlite3.Connection,
    signals: Iterable[dict],
    *,
    trace_id: str | None = None,
) -> str:
    """Write one feed_impressions row per signal, in order.

    Each signal dict must carry 'signal_id'; 'feed_score' is optional and
    defaults to 0.0. Returns the trace_id used (generated if not given).
    """
    trace_id = trace_id or str(uuid.uuid4())
    session_id, base_rank = _resolve_session(conn)

    rows = []
    for i, sig in enumerate(signals):
        sid = sig.get("signal_id")
        if sid is None:
            continue
        rows.append((
            trace_id,
            session_id,
            int(sid),
            i,
            base_rank + i,
            float(sig.get("feed_score", 0.0)),
        ))

    if rows:
        conn.executemany(
            "INSERT INTO feed_impressions "
            "(trace_id, session_id, signal_id, rank_in_trace, rank_in_session, feed_score) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    return trace_id
