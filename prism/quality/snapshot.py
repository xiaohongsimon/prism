"""Capture numeric health metrics into quality_snapshots.

Each metric is one row. Rules layer reads them by (dimension, key, metric)
and compares recent values to historical baselines.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Iterable


def _write(
    conn: sqlite3.Connection,
    dimension: str,
    key: str,
    metric: str,
    value: float,
    context: dict | None = None,
) -> None:
    conn.execute(
        "INSERT INTO quality_snapshots (dimension, key, metric, value, context_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (dimension, key, metric, float(value),
         json.dumps(context or {}, ensure_ascii=False)),
    )


def _per_source_raw_items(conn: sqlite3.Connection) -> Iterable[tuple[str, str, int]]:
    """Raw items per source in the last 6 hours.

    Yields (source_key, source_type, count).
    """
    rows = conn.execute(
        """SELECT src.source_key, src.type AS source_type, COUNT(ri.id) AS n
             FROM sources src
        LEFT JOIN raw_items ri
               ON ri.source_id = src.id
              AND ri.created_at >= datetime('now','-6 hours')
            WHERE src.enabled = 1
         GROUP BY src.source_key, src.type"""
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def _source_type_composition(conn: sqlite3.Connection) -> Iterable[tuple[str, int]]:
    """Current signals grouped by source type — the feed pool composition."""
    rows = conn.execute(
        """SELECT src.type, COUNT(DISTINCT s.id) AS n
             FROM signals s
             JOIN clusters c ON c.id = s.cluster_id
             JOIN cluster_items ci ON ci.cluster_id = c.id
             JOIN raw_items ri ON ri.id = ci.raw_item_id
             JOIN sources src ON src.id = ri.source_id
            WHERE s.is_current = 1
              AND s.signal_layer IN ('actionable','noteworthy')
         GROUP BY src.type"""
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _user_activity(conn: sqlite3.Connection) -> dict[str, int]:
    """User engagement in the last 24h.

    After Wave 1 (2026-04-23) `pairwise_comparisons` is gone; feed
    interactions are the only engagement channel. `pairwise_24h` is kept
    in the returned shape (hard-coded 0) so downstream dashboards that
    still read the key don't blow up during the transition.
    """
    feed_actions = conn.execute(
        "SELECT COUNT(*) FROM feed_interactions "
        "WHERE created_at >= datetime('now','-1 day')"
    ).fetchone()[0]
    return {"feed_actions_24h": feed_actions, "pairwise_24h": 0}


def _analyze_throughput(conn: sqlite3.Connection) -> dict[str, int]:
    """Recent pipeline output volume."""
    sig_24h = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE created_at >= datetime('now','-1 day')"
    ).fetchone()[0]
    sig_7d = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE created_at >= datetime('now','-7 days')"
    ).fetchone()[0]
    return {"signals_created_24h": sig_24h, "signals_created_7d": sig_7d}


def _source_failure_counts(conn: sqlite3.Connection) -> dict[str, int]:
    total = conn.execute(
        "SELECT COUNT(*) FROM sources WHERE enabled = 1"
    ).fetchone()[0]
    failing = conn.execute(
        "SELECT COUNT(*) FROM sources "
        "WHERE enabled = 1 AND consecutive_failures >= 3"
    ).fetchone()[0]
    return {"sources_total": total, "sources_failing": failing}


def capture(conn: sqlite3.Connection) -> int:
    """Capture all health metrics. Returns number of rows written."""
    n = 0

    for source_key, source_type, count in _per_source_raw_items(conn):
        _write(conn, "source", source_key, "raw_items_6h", count,
               {"source_type": source_type})
        n += 1

    for stype, count in _source_type_composition(conn):
        _write(conn, "source_type", stype, "current_signals", count)
        n += 1

    act = _user_activity(conn)
    for metric, v in act.items():
        _write(conn, "user", "", metric, v)
        n += 1

    thr = _analyze_throughput(conn)
    for metric, v in thr.items():
        _write(conn, "pipeline", "", metric, v)
        n += 1

    fc = _source_failure_counts(conn)
    for metric, v in fc.items():
        _write(conn, "pipeline", "", metric, v)
        n += 1

    conn.commit()
    return n
