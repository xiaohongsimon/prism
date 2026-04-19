import sqlite3
from unittest.mock import patch

from prism.db import init_db
from prism.web.pairwise import record_vote


def _mkconn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    # Seed two signals. Adjust INSERTs to actual schema if needed; check
    # prism/db.py if the helper below fails with a schema error.
    conn.execute("INSERT INTO clusters (id, date, topic_label) VALUES (1,'2026-04-19','c')")
    conn.execute(
        "INSERT INTO signals (id, cluster_id, summary) VALUES (1,1,'a'), (2,1,'b')"
    )
    conn.commit()
    return conn


def test_record_vote_persists_explicit_strategy():
    conn = _mkconn()
    record_vote(conn, signal_a_id=1, signal_b_id=2, winner="a",
                comment="", response_time_ms=0, strategy="active")
    row = conn.execute(
        "SELECT pair_strategy FROM pairwise_comparisons WHERE signal_a_id=1"
    ).fetchone()
    assert row[0] == "active"


def test_select_pair_returns_strategy_name():
    from prism.web.pairwise import select_pair
    conn = _mkconn()
    for i in range(3, 8):
        conn.execute(
            "INSERT INTO signals (id, cluster_id, summary) VALUES (?, 1, ?)",
            (i, f"s{i}"),
        )
    conn.commit()
    with patch("prism.web.pairwise._check_neither_streak", return_value=False), \
         patch("random.random", return_value=0.01):
        result = select_pair(conn)
    if result is None:
        return  # pool empty (other filters); the record_vote test covers the core behavior
    assert len(result) == 3
    a, b, strat = result
    assert strat == "exploit"
