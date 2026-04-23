import math
import sqlite3
from prism.db import init_db
from prism.web.ranking import compute_feed, update_preferences


def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _seed(conn):
    """Insert 3 signals with different strengths and sources."""
    conn.execute("INSERT INTO sources (source_key, type, handle) VALUES ('x:karpathy', 'x', 'karpathy')")
    conn.execute("INSERT INTO sources (source_key, type, handle) VALUES ('hn:best', 'hackernews', '')")

    conn.execute("INSERT INTO raw_items (source_id, url, title, published_at) VALUES (1, 'http://a', 'A', '2026-03-29T06:00:00')")
    conn.execute("INSERT INTO raw_items (source_id, url, title, published_at) VALUES (2, 'http://b', 'B', '2026-03-29T03:00:00')")
    conn.execute("INSERT INTO raw_items (source_id, url, title, published_at) VALUES (1, 'http://c', 'C', '2026-03-28T12:00:00')")

    conn.execute("INSERT INTO clusters (date, topic_label, item_count) VALUES ('2026-03-29', 'GPT-5', 3)")
    conn.execute("INSERT INTO clusters (date, topic_label, item_count) VALUES ('2026-03-29', 'vLLM', 1)")
    conn.execute("INSERT INTO clusters (date, topic_label, item_count) VALUES ('2026-03-28', 'Old Topic', 1)")

    conn.execute("INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (1, 1)")
    conn.execute("INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (2, 2)")
    conn.execute("INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (3, 3)")

    conn.execute("INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, tags_json, is_current) VALUES (1, 'GPT-5 leak', 'actionable', 5, '[\"gpt\",\"benchmark\"]', 1)")
    conn.execute("INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, tags_json, is_current) VALUES (2, 'vLLM release', 'strategic', 3, '[\"vllm\",\"infra\"]', 1)")
    conn.execute("INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, tags_json, is_current) VALUES (3, 'Old stuff', 'noise', 1, '[]', 1)")

    conn.execute("INSERT INTO trends (topic_label, date, heat_score, is_current) VALUES ('GPT-5', '2026-03-29', 15.0, 1)")
    conn.execute("INSERT INTO trends (topic_label, date, heat_score, is_current) VALUES ('vLLM', '2026-03-29', 3.0, 1)")
    conn.commit()


def test_compute_feed_returns_sorted_by_score():
    conn = _fresh_db()
    _seed(conn)
    items = compute_feed(conn, tab="hot", page=1, per_page=10)
    assert len(items) >= 2
    scores = [it["score"] for it in items]
    assert scores == sorted(scores, reverse=True)


def test_compute_feed_recommend_tab_uses_preference():
    conn = _fresh_db()
    _seed(conn)
    # Boost 'vllm' tag so vLLM signal rises above GPT-5
    conn.execute("INSERT INTO preference_weights (dimension, key, weight) VALUES ('tag', 'vllm', 10.0)")
    conn.commit()
    items = compute_feed(conn, tab="recommend", page=1, per_page=10)
    assert items[0]["topic_label"] == "vLLM"


def test_compute_feed_follow_tab_filters_sources():
    conn = _fresh_db()
    _seed(conn)
    # Disable hn:best source
    conn.execute("UPDATE sources SET enabled = 0 WHERE source_key = 'hn:best'")
    conn.commit()
    items = compute_feed(conn, tab="follow", page=1, per_page=10)
    source_keys = set()
    for it in items:
        for sk in it.get("source_keys", []):
            source_keys.add(sk)
    assert "hn:best" not in source_keys


def test_compute_feed_pagination():
    conn = _fresh_db()
    _seed(conn)
    page1 = compute_feed(conn, tab="hot", page=1, per_page=1)
    page2 = compute_feed(conn, tab="hot", page=2, per_page=1)
    assert len(page1) == 1
    assert len(page2) >= 1
    assert page1[0]["signal_id"] != page2[0]["signal_id"]


def test_update_preferences_like():
    conn = _fresh_db()
    _seed(conn)
    update_preferences(conn, signal_id=1, action="like")
    row = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='layer' AND key='actionable'"
    ).fetchone()
    assert row is not None
    assert row["weight"] == 1.0


def test_update_preferences_dislike():
    conn = _fresh_db()
    _seed(conn)
    update_preferences(conn, signal_id=2, action="dislike")
    row = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='tag' AND key='vllm'"
    ).fetchone()
    assert row is not None
    assert row["weight"] == -1.0


def test_hot_tab_uses_heat_and_decay_only():
    """Post Wave 1 cleanup the `hot` tab weights are (heat, 0, decay).
    The BT dimension was removed, so for identical decay the signal with
    higher heat (strength * item_count) should lead.
    """
    conn = _fresh_db()
    _seed(conn)
    items = compute_feed(conn, tab="hot", page=1, per_page=10)
    # Seed gives signal 1 ("GPT-5") strength=5 vs signal 2 ("vLLM") strength=3
    # — with heat dominating, GPT-5 must come out on top.
    assert items[0]["topic_label"] == "GPT-5"


def test_update_preferences_save():
    conn = _fresh_db()
    _seed(conn)
    update_preferences(conn, signal_id=1, action="save")
    row = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='tag' AND key='gpt'"
    ).fetchone()
    assert row is not None
    assert row["weight"] == 2.0
