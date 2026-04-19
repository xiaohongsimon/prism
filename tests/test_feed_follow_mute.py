import sqlite3

from prism.db import init_db
from prism.web.feed import record_feed_action


def _mkconn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_follow_author_sets_author_weight_3():
    conn = _mkconn()
    record_feed_action(conn, signal_id=0, action="follow_author", target_key="karpathy")
    w = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='author' AND key='karpathy'"
    ).fetchone()
    assert w["weight"] == 3.0


def test_unfollow_author_clears_weight():
    conn = _mkconn()
    record_feed_action(conn, signal_id=0, action="follow_author", target_key="karpathy")
    record_feed_action(conn, signal_id=0, action="unfollow_author", target_key="karpathy")
    w = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='author' AND key='karpathy'"
    ).fetchone()
    assert w["weight"] == 0.0


def test_mute_topic_sets_tag_weight_negative():
    conn = _mkconn()
    record_feed_action(conn, signal_id=0, action="mute_topic", target_key="crypto")
    w = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='tag' AND key='crypto'"
    ).fetchone()
    assert w["weight"] == -2.0


def test_unmute_topic_clears_weight():
    conn = _mkconn()
    record_feed_action(conn, signal_id=0, action="mute_topic", target_key="crypto")
    record_feed_action(conn, signal_id=0, action="unmute_topic", target_key="crypto")
    w = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='tag' AND key='crypto'"
    ).fetchone()
    assert w["weight"] == 0.0


def test_feed_interactions_row_logged():
    conn = _mkconn()
    record_feed_action(conn, signal_id=0, action="follow_author", target_key="karpathy")
    record_feed_action(conn, signal_id=0, action="mute_topic", target_key="crypto")
    n = conn.execute("SELECT COUNT(*) FROM feed_interactions").fetchone()[0]
    assert n == 2
