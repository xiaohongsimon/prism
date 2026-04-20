"""Anomaly rules — read snapshots, open/update quality_anomalies rows.

Rules are deliberately simple: compare a recent value to a baseline
(historical median or threshold) and if it falls outside, open an
anomaly. Each rule is idempotent via the UNIQUE(dimension, key, rule,
status='open') index — re-running just bumps last_seen_at.
"""
from __future__ import annotations

import sqlite3
from statistics import median

# Thresholds — kept as module constants so tests and the /quality page
# can surface the numbers and so they can be tuned in one place.
SILENT_SOURCE_HOURS = 6            # window for "silent source" check
SILENT_CRITICAL_DROP_PCT = 70      # >= N% drop vs baseline triggers warn
SILENT_CRITICAL_ZERO_DAYS = 2      # zero raw items for N days → critical
USER_IDLE_HOURS = 24               # no feed actions for N hours → info
FAILING_SOURCE_SHARE_PCT = 30      # % of sources failing → critical
ANALYZE_THROUGHPUT_DROP_PCT = 50   # signals_created_24h vs week baseline


def _open_or_touch(
    conn: sqlite3.Connection,
    *,
    dimension: str,
    key: str,
    rule: str,
    severity: str,
    title: str,
    detail: str,
) -> None:
    """Open a new anomaly or bump last_seen_at if already open."""
    conn.execute(
        "INSERT INTO quality_anomalies "
        "(dimension, key, rule, severity, title, detail) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(dimension, key, rule, status) DO UPDATE SET "
        "last_seen_at = datetime('now'), severity = excluded.severity, "
        "title = excluded.title, detail = excluded.detail",
        (dimension, key, rule, severity, title, detail),
    )


def _close_if_open(
    conn: sqlite3.Connection, dimension: str, key: str, rule: str
) -> None:
    conn.execute(
        "UPDATE quality_anomalies "
        "SET status='resolved', resolved_at=datetime('now') "
        "WHERE dimension = ? AND key = ? AND rule = ? AND status='open'",
        (dimension, key, rule),
    )


# ────────────────────────────────────────────────────────────────────
# Source-level rules
# ────────────────────────────────────────────────────────────────────

def _rule_silent_source(conn: sqlite3.Connection) -> None:
    """A source produced zero raw items in the last 6h while its
    baseline (median of prior 6h windows over last 7 days) was
    non-trivial."""
    # Latest per-source 6h count from most recent snapshot.
    latest = conn.execute(
        "SELECT key, value, context_json FROM quality_snapshots "
        "WHERE metric = 'raw_items_6h' "
        "AND captured_at = (SELECT MAX(captured_at) FROM quality_snapshots "
        "                    WHERE metric='raw_items_6h')"
    ).fetchall()

    for row in latest:
        key = row[0]
        cur = row[1]
        # Historical baseline: same metric, 7 days prior to now, exclude latest.
        hist_rows = conn.execute(
            "SELECT value FROM quality_snapshots "
            "WHERE metric='raw_items_6h' AND key = ? "
            "AND captured_at >= datetime('now','-7 days') "
            "AND captured_at < (SELECT MAX(captured_at) FROM quality_snapshots "
            "                    WHERE metric='raw_items_6h')",
            (key,),
        ).fetchall()
        hist_values = [r[0] for r in hist_rows]
        if not hist_values:
            continue
        baseline = median(hist_values)
        if baseline < 1:
            # Source was always quiet; don't alert.
            _close_if_open(conn, "source", key, "silent_source")
            continue

        if cur == 0 and baseline >= 1:
            sev = "critical" if baseline >= 5 else "warn"
            _open_or_touch(
                conn,
                dimension="source",
                key=key,
                rule="silent_source",
                severity=sev,
                title=f"{key} 在过去 {SILENT_SOURCE_HOURS}h 零产出",
                detail=(
                    f"7-day median was {baseline:.1f} items per 6h. "
                    "Adapter likely broken or upstream rate-limited."
                ),
            )
            continue

        drop_pct = (1.0 - (cur / baseline)) * 100 if baseline > 0 else 0
        if drop_pct >= SILENT_CRITICAL_DROP_PCT:
            _open_or_touch(
                conn,
                dimension="source",
                key=key,
                rule="silent_source",
                severity="warn",
                title=f"{key} 产出下降 {drop_pct:.0f}%",
                detail=(
                    f"Last 6h: {cur:.0f} items. Baseline (7d median): "
                    f"{baseline:.1f}. Below alert threshold."
                ),
            )
        else:
            _close_if_open(conn, "source", key, "silent_source")


def _rule_failing_source_share(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT value FROM quality_snapshots "
        "WHERE metric='sources_failing' ORDER BY captured_at DESC, id DESC LIMIT 1"
    ).fetchone()
    total_row = conn.execute(
        "SELECT value FROM quality_snapshots "
        "WHERE metric='sources_total' ORDER BY captured_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if not row or not total_row or total_row[0] == 0:
        return
    failing, total = row[0], total_row[0]
    pct = failing / total * 100
    if pct >= FAILING_SOURCE_SHARE_PCT:
        _open_or_touch(
            conn,
            dimension="pipeline",
            key="sources",
            rule="failing_share",
            severity="critical",
            title=f"{pct:.0f}% 源处于失败状态",
            detail=(
                f"{failing:.0f} of {total:.0f} enabled sources have "
                ">= 3 consecutive failures. Check connectivity / API keys."
            ),
        )
    else:
        _close_if_open(conn, "pipeline", "sources", "failing_share")


# ────────────────────────────────────────────────────────────────────
# User-level rules
# ────────────────────────────────────────────────────────────────────

def _rule_user_idle(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT value FROM quality_snapshots "
        "WHERE metric='feed_actions_24h' "
        "ORDER BY captured_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return
    if row[0] == 0:
        _open_or_touch(
            conn,
            dimension="user",
            key="",
            rule="idle_24h",
            severity="info",
            title="过去 24h 无 feed 交互",
            detail=(
                "没有 save / dismiss / follow / mute。系统无法学习新偏好。"
                "如果你在休假这条可以忽略；如果不是——排序可能已经跑偏。"
            ),
        )
    else:
        _close_if_open(conn, "user", "", "idle_24h")


# ────────────────────────────────────────────────────────────────────
# Pipeline-level rules
# ────────────────────────────────────────────────────────────────────

def _rule_analyze_throughput(conn: sqlite3.Connection) -> None:
    row24 = conn.execute(
        "SELECT value FROM quality_snapshots "
        "WHERE metric='signals_created_24h' ORDER BY captured_at DESC, id DESC LIMIT 1"
    ).fetchone()
    row7 = conn.execute(
        "SELECT value FROM quality_snapshots "
        "WHERE metric='signals_created_7d' ORDER BY captured_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if not row24 or not row7:
        return
    last24 = row24[0]
    week = row7[0]
    week_daily = week / 7.0
    if week_daily < 5:
        return  # too small a sample
    drop_pct = (1.0 - (last24 / week_daily)) * 100 if week_daily else 0
    if drop_pct >= ANALYZE_THROUGHPUT_DROP_PCT:
        _open_or_touch(
            conn,
            dimension="pipeline",
            key="analyze",
            rule="throughput_drop",
            severity="warn",
            title=f"signals 产出下降 {drop_pct:.0f}%",
            detail=(
                f"过去 24h 产出 {last24:.0f} 条 signals, 7 日均值 "
                f"{week_daily:.1f}。可能是 cluster/analyze 或上游 ingest 出问题。"
            ),
        )
    else:
        _close_if_open(conn, "pipeline", "analyze", "throughput_drop")


RULES = [
    _rule_silent_source,
    _rule_failing_source_share,
    _rule_user_idle,
    _rule_analyze_throughput,
]


def evaluate(conn: sqlite3.Connection) -> int:
    """Run every rule. Returns number of rules evaluated."""
    for rule_fn in RULES:
        rule_fn(conn)
    conn.commit()
    return len(RULES)


def list_open(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, first_seen_at, last_seen_at, severity, dimension, "
        "       key, rule, title, detail "
        "  FROM quality_anomalies "
        " WHERE status='open' "
        " ORDER BY CASE severity "
        "            WHEN 'critical' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END, "
        "          last_seen_at DESC"
    ).fetchall()
    return [
        {
            "id": r[0], "first_seen_at": r[1], "last_seen_at": r[2],
            "severity": r[3], "dimension": r[4], "key": r[5],
            "rule": r[6], "title": r[7], "detail": r[8],
        }
        for r in rows
    ]


def ack(conn: sqlite3.Connection, anomaly_id: int) -> None:
    conn.execute(
        "UPDATE quality_anomalies SET status='ack', acked_at=datetime('now') "
        "WHERE id = ? AND status='open'",
        (anomaly_id,),
    )
    conn.commit()
