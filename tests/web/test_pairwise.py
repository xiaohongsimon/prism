"""Tests for pairwise recommendation engine."""

import json
import sqlite3
import pytest
from prism.db import init_db


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _seed_signals(conn, n=5):
    """Insert n signals with clusters and sources for testing."""
    conn.execute("INSERT INTO sources (source_key, type, handle) VALUES ('hn:best', 'hackernews', '')")
    conn.execute("INSERT INTO sources (source_key, type, handle) VALUES ('x:karpathy', 'x', 'karpathy')")
    for i in range(1, n + 1):
        conn.execute(
            "INSERT INTO raw_items (source_id, url, title, published_at) VALUES (?, ?, ?, datetime('now'))",
            (1 if i % 2 else 2, f"http://item{i}", f"Item {i}"),
        )
        conn.execute(
            "INSERT INTO clusters (date, topic_label, item_count) VALUES (date('now'), ?, 1)",
            (f"Topic {i}",),
        )
        conn.execute("INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (?, ?)", (i, i))
        conn.execute(
            "INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, "
            "tags_json, is_current) VALUES (?, ?, 'actionable', 3, ?, 1)",
            (i, f"Signal {i} summary", json.dumps([f"tag{i}"])),
        )
    conn.commit()


# --- BT Scoring ---

def test_bt_score_update():
    from prism.web.pairwise import update_bt_scores
    new_a, new_b = update_bt_scores(1500.0, 1500.0, "a")
    assert new_a > 1500.0
    assert new_b < 1500.0
    assert abs((new_a - 1500.0) + (new_b - 1500.0)) < 0.01  # zero-sum


def test_bt_both():
    from prism.web.pairwise import update_bt_scores
    new_a, new_b = update_bt_scores(1500.0, 1500.0, "both")
    assert abs(new_a - 1500.0) < 0.01
    assert abs(new_b - 1500.0) < 0.01


def test_bt_neither_skip():
    from prism.web.pairwise import update_bt_scores
    new_a, new_b = update_bt_scores(1500.0, 1500.0, "neither")
    assert new_a == 1500.0
    assert new_b == 1500.0
    new_a2, new_b2 = update_bt_scores(1600.0, 1400.0, "skip")
    assert new_a2 == 1600.0
    assert new_b2 == 1400.0


def test_bt_underdog_wins_more():
    """Lower-rated signal gains more when winning."""
    from prism.web.pairwise import update_bt_scores
    new_a, new_b = update_bt_scores(1300.0, 1700.0, "a")
    gain_a = new_a - 1300.0
    new_c, new_d = update_bt_scores(1700.0, 1300.0, "a")
    gain_c = new_c - 1700.0
    assert gain_a > gain_c  # underdog gains more


# --- Pair Selection ---

def test_select_pair_returns_two_different_signals():
    from prism.web.pairwise import select_pair
    conn = _fresh_db()
    _seed_signals(conn, 5)
    result = select_pair(conn)
    assert result is not None
    a, b, _strategy = result
    assert a["signal_id"] != b["signal_id"]


def test_select_pair_insufficient():
    from prism.web.pairwise import select_pair
    conn = _fresh_db()
    _seed_signals(conn, 1)  # only 1 signal — not enough for a pair
    result = select_pair(conn)
    assert result is None


def test_pair_break_loop():
    """After 3 consecutive 'neither', force random strategy."""
    from prism.web.pairwise import select_pair, record_vote
    conn = _fresh_db()
    _seed_signals(conn, 10)
    # Record 3 'neither' votes
    for _ in range(3):
        pair = select_pair(conn)
        assert pair is not None
        a, b, _strategy = pair
        record_vote(conn, a["signal_id"], b["signal_id"], "neither", "", 0)
    # Next pair should still work (random fallback)
    pair = select_pair(conn)
    assert pair is not None


# --- Record Vote ---

def test_record_vote_updates_bt_and_preferences():
    from prism.web.pairwise import record_vote
    conn = _fresh_db()
    _seed_signals(conn, 2)
    record_vote(conn, signal_a_id=1, signal_b_id=2, winner="a",
                comment="better technical depth", response_time_ms=3000)

    # Check BT scores updated
    row_a = conn.execute("SELECT bt_score FROM signal_scores WHERE signal_id = 1").fetchone()
    row_b = conn.execute("SELECT bt_score FROM signal_scores WHERE signal_id = 2").fetchone()
    assert row_a is not None
    assert row_b is not None
    assert row_a["bt_score"] > 1500.0
    assert row_b["bt_score"] < 1500.0

    # Check pairwise_comparisons recorded
    row = conn.execute("SELECT * FROM pairwise_comparisons ORDER BY id DESC LIMIT 1").fetchone()
    assert row["winner"] == "a"
    assert row["user_comment"] == "better technical depth"

    # Check source_weights updated
    sw = conn.execute("SELECT * FROM source_weights WHERE source_key = 'hn:best'").fetchone()
    assert sw is not None
    assert sw["total_comparisons"] > 0


# --- External Feed ---

def test_external_feed_preference():
    from prism.web.pairwise import process_external_feed
    conn = _fresh_db()
    _seed_signals(conn, 2)
    process_external_feed(conn, url="https://example.com/article", note="interesting infra work")

    row = conn.execute("SELECT * FROM external_feeds WHERE url = 'https://example.com/article'").fetchone()
    assert row is not None
    assert row["user_note"] == "interesting infra work"


def test_external_feed_url_dedup():
    from prism.web.pairwise import process_external_feed
    conn = _fresh_db()
    process_external_feed(conn, url="https://example.com/a", note="first")
    process_external_feed(conn, url="https://example.com/a", note="updated")
    rows = conn.execute("SELECT * FROM external_feeds WHERE url = 'https://example.com/a'").fetchall()
    assert len(rows) == 1
    assert rows[0]["user_note"] == "updated"


# --- Decision Log ---

def test_decision_log_on_source_weight_adjust():
    from prism.web.pairwise import adjust_source_weights
    conn = _fresh_db()
    _seed_signals(conn, 5)
    # Simulate enough comparisons for hn:best with high win rate
    conn.execute(
        "INSERT OR REPLACE INTO source_weights (source_key, weight, win_rate, total_comparisons) "
        "VALUES ('hn:best', 1.0, 0.8, 15)"
    )
    conn.commit()
    adjust_source_weights(conn)
    logs = conn.execute("SELECT * FROM decision_log WHERE action = 'adjust_source_weight'").fetchall()
    assert len(logs) >= 1
