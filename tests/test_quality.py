"""Tests for the Quality Watchdog — snapshot capture and rule evaluation."""
import sqlite3

from prism.db import init_db
from prism.quality.rules import evaluate, list_open
from prism.quality.scan import scan
from prism.quality.snapshot import capture


def _mkconn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _seed_source(conn, sid, key, stype="x", handle=""):
    conn.execute(
        "INSERT OR IGNORE INTO sources (id, source_key, type, handle, enabled, "
        "consecutive_failures) VALUES (?, ?, ?, ?, 1, 0)",
        (sid, key, stype, handle),
    )


def _seed_raw_recent(conn, rid, source_id):
    conn.execute(
        "INSERT INTO raw_items (id, source_id, url, created_at) "
        "VALUES (?, ?, ?, datetime('now','-1 hour'))",
        (rid, source_id, f"https://example.com/{rid}"),
    )


# ──────── snapshot.capture ────────

def test_capture_writes_source_raw_items_row():
    conn = _mkconn()
    _seed_source(conn, 1, "x:alice")
    _seed_raw_recent(conn, 100, 1)
    n = capture(conn)
    assert n >= 1
    row = conn.execute(
        "SELECT value FROM quality_snapshots "
        "WHERE dimension='source' AND key='x:alice' AND metric='raw_items_6h'"
    ).fetchone()
    assert row is not None
    assert row[0] == 1.0


def test_capture_records_user_activity_zero_by_default():
    conn = _mkconn()
    capture(conn)
    row = conn.execute(
        "SELECT value FROM quality_snapshots WHERE metric='feed_actions_24h'"
    ).fetchone()
    assert row is not None
    assert row[0] == 0.0


# ──────── rules.evaluate — user idle ────────

def test_user_idle_rule_opens_anomaly_when_zero_feed_actions():
    conn = _mkconn()
    capture(conn)  # feed_actions_24h = 0
    evaluate(conn)
    anomalies = list_open(conn)
    rules = {a["rule"] for a in anomalies}
    assert "idle_24h" in rules


def test_user_idle_rule_closes_when_activity_returns():
    conn = _mkconn()
    capture(conn)
    evaluate(conn)
    assert any(a["rule"] == "idle_24h" for a in list_open(conn))

    # Seed an interaction and re-capture + re-evaluate.
    conn.execute(
        "INSERT INTO feed_interactions (signal_id, action, created_at) "
        "VALUES (1, 'save', datetime('now'))"
    )
    capture(conn)
    evaluate(conn)
    assert not any(a["rule"] == "idle_24h" for a in list_open(conn))


# ──────── rules.evaluate — failing-source share ────────

def test_failing_source_share_opens_when_over_threshold():
    conn = _mkconn()
    # 4 enabled sources, 2 failing = 50% >= 30% threshold
    for i, key in enumerate(["a", "b", "c", "d"], start=1):
        conn.execute(
            "INSERT INTO sources (id, source_key, type, enabled, "
            "consecutive_failures) VALUES (?, ?, 'x', 1, ?)",
            (i, key, 3 if i <= 2 else 0),
        )
    capture(conn)
    evaluate(conn)
    anomalies = list_open(conn)
    assert any(a["rule"] == "failing_share" for a in anomalies)


# ──────── scan entry-point ────────

def test_scan_returns_counts():
    conn = _mkconn()
    result = scan(conn)
    assert result["metrics"] >= 1
    assert result["rules"] >= 1


def test_rule_firing_is_idempotent():
    conn = _mkconn()
    capture(conn)
    evaluate(conn)
    first = list_open(conn)

    # Re-run without changing anything.
    capture(conn)
    evaluate(conn)
    second = list_open(conn)

    # Same row IDs — no duplicates opened.
    assert {a["id"] for a in first} == {a["id"] for a in second}
