"""Skip-above sample construction.

For every `save` event, look up the impression of that signal in
feed_impressions and treat all signals that were shown ABOVE it in the
same session (and not themselves saved) as negatives. Each save event
becomes one training group — perfect input shape for xgb.XGBRanker's
rank:pairwise objective.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class Sample:
    group_id: int           # one group per save event
    signal_id: int
    label: int              # 1 = saved, 0 = skip-above
    session_id: str
    rank_in_session: int
    feed_score: float
    served_at: str
    save_event_id: int      # feed_interactions.id


def _load_from_materialized(conn: sqlite3.Connection) -> list[Sample]:
    """Read pre-computed rows from ctr_samples, if any are present."""
    rows = conn.execute(
        "SELECT group_id, signal_id, label, session_id, rank_in_session, "
        "       feed_score, served_at "
        "FROM ctr_samples ORDER BY group_id, label DESC, rank_in_session"
    ).fetchall()
    return [
        Sample(
            group_id=int(r["group_id"]),
            signal_id=int(r["signal_id"]),
            label=int(r["label"]),
            session_id=str(r["session_id"]),
            rank_in_session=int(r["rank_in_session"]),
            feed_score=float(r["feed_score"] or 0.0),
            served_at=str(r["served_at"]),
            save_event_id=int(r["group_id"]),
        )
        for r in rows
    ]


def build_samples(conn: sqlite3.Connection) -> list[Sample]:
    """Return the full list of training samples, grouped by save event.

    Prefers the materialized ctr_samples table (populated by the
    background task on every save). Falls back to on-the-fly skip-above
    computation when the table is empty — that path is kept for tests
    that seed impressions/saves directly without going through the
    /feed/action route.

    Only save events that can be tied back to an impression are kept —
    saves from /article/like or direct URLs won't have a trace and so
    contribute nothing.
    """
    try:
        materialized = _load_from_materialized(conn)
        if materialized:
            return materialized
    except sqlite3.OperationalError:
        # ctr_samples table missing — legacy DB or fresh in-memory conn.
        pass

    saves = conn.execute(
        "SELECT id, signal_id, created_at FROM feed_interactions "
        "WHERE action = 'save' ORDER BY id"
    ).fetchall()

    # Pre-load ALL save signal_ids so we can exclude other saves from a
    # group's negatives cheaply.
    all_saved_ids = {row["signal_id"] for row in saves}

    samples: list[Sample] = []
    for save in saves:
        save_id = save["id"]
        sid = save["signal_id"]
        saved_at = save["created_at"]

        # Most-recent impression of this signal at or before the save time.
        impr = conn.execute(
            "SELECT session_id, rank_in_session, served_at, feed_score "
            "FROM feed_impressions "
            "WHERE signal_id = ? AND served_at <= ? "
            "ORDER BY served_at DESC LIMIT 1",
            (sid, saved_at),
        ).fetchone()
        if not impr:
            continue

        session_id = impr["session_id"]
        pos_rank = impr["rank_in_session"]

        # Positive row.
        samples.append(Sample(
            group_id=save_id,
            signal_id=sid,
            label=1,
            session_id=session_id,
            rank_in_session=pos_rank,
            feed_score=impr["feed_score"],
            served_at=impr["served_at"],
            save_event_id=save_id,
        ))

        # Negatives — dedup by signal_id keeping the earliest rank in the
        # session (first impression position is what the user "skipped").
        peers = conn.execute(
            "SELECT signal_id, MIN(rank_in_session) AS r, "
            "       MIN(served_at) AS t, MAX(feed_score) AS sc "
            "FROM feed_impressions "
            "WHERE session_id = ? AND rank_in_session < ? "
            "GROUP BY signal_id",
            (session_id, pos_rank),
        ).fetchall()

        for p in peers:
            if p["signal_id"] == sid:
                continue
            if p["signal_id"] in all_saved_ids:
                continue  # other saves are their own group, never negatives
            samples.append(Sample(
                group_id=save_id,
                signal_id=p["signal_id"],
                label=0,
                session_id=session_id,
                rank_in_session=p["r"],
                feed_score=p["sc"] or 0.0,
                served_at=p["t"],
                save_event_id=save_id,
            ))

    # Drop groups that ended up with only a positive (no negatives) — they
    # contribute nothing to a pairwise-ranking loss.
    group_counts: dict[int, int] = {}
    for s in samples:
        group_counts[s.group_id] = group_counts.get(s.group_id, 0) + 1
    return [s for s in samples if group_counts[s.group_id] > 1]


def summarize(samples: list[Sample]) -> dict:
    groups = {s.group_id for s in samples}
    pos = sum(1 for s in samples if s.label == 1)
    neg = sum(1 for s in samples if s.label == 0)
    sizes = [sum(1 for s in samples if s.group_id == g) for g in groups]
    return {
        "groups": len(groups),
        "positives": pos,
        "negatives": neg,
        "avg_group_size": (sum(sizes) / len(sizes)) if sizes else 0,
        "max_group_size": max(sizes) if sizes else 0,
    }
