"""Tests for per-signal feature extraction."""
from __future__ import annotations

import json
import sqlite3

from prism.db import init_db


def _mkconn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _seed(conn, *, sid, source_key="x:demo", stype="x", author="alice",
          tags=("ai",), strength=3, layer="actionable", summary="hello",
          content_zh="", raw_json=None):
    conn.execute(
        "INSERT OR IGNORE INTO sources (id, source_key, type, handle) "
        "VALUES (1, ?, ?, 'demo')",
        (source_key, stype),
    )
    conn.execute(
        "INSERT OR IGNORE INTO clusters (id, date, topic_label, item_count) "
        "VALUES (?, '2026-04-19', 'AI', 1)",
        (sid,),
    )
    conn.execute(
        "INSERT INTO signals (id, cluster_id, summary, content_zh, signal_layer, "
        "signal_strength, tags_json, is_current, analysis_type, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'daily', datetime('now'))",
        (sid, sid, summary, content_zh, layer, strength, json.dumps(list(tags))),
    )
    conn.execute(
        "INSERT INTO raw_items (id, source_id, url, author, body, raw_json) "
        "VALUES (?, 1, ?, ?, 't', ?)",
        (sid, f"https://x/{sid}", author, raw_json or "{}"),
    )
    conn.execute(
        "INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (?, ?)",
        (sid, sid),
    )


def test_extract_returns_all_feature_names():
    from prism.ctr.features import FEATURE_NAMES, extract

    conn = _mkconn()
    _seed(conn, sid=1)
    conn.commit()
    feats = extract(conn, 1)
    assert set(feats.keys()) == set(FEATURE_NAMES)


def test_extract_source_type_one_hot():
    from prism.ctr.features import extract

    conn = _mkconn()
    _seed(conn, sid=1, source_key="x:a", stype="x")
    conn.commit()
    feats = extract(conn, 1)
    assert feats["stype_x"] == 1.0
    assert feats["stype_youtube"] == 0.0
    assert feats["stype_other"] == 0.0


def test_extract_layer_one_hot():
    from prism.ctr.features import extract

    conn = _mkconn()
    _seed(conn, sid=1, layer="actionable")
    conn.commit()
    feats = extract(conn, 1)
    assert feats["layer_actionable"] == 1.0
    assert feats["layer_strategic"] == 0.0
    assert feats["layer_noise"] == 0.0


def test_extract_preference_aggregates():
    """preference_weights rows should flow into pref_* features."""
    from prism.ctr.features import extract

    conn = _mkconn()
    _seed(conn, sid=1, author="alice", tags=("ai", "rl"))
    conn.execute(
        "INSERT INTO preference_weights (dimension, key, weight) VALUES "
        "('author', 'alice', 3.0), "
        "('tag', 'ai', 1.0), ('tag', 'rl', 2.0), "
        "('layer', 'actionable', 0.5)"
    )
    conn.commit()

    feats = extract(conn, 1)
    assert feats["pref_author_max"] == 3.0
    assert feats["pref_author_sum"] == 3.0
    assert feats["pref_tag_max"] == 2.0
    assert feats["pref_tag_sum"] == 3.0
    assert feats["pref_layer"] == 0.5


def test_extract_x_engagement_from_raw_json():
    """favourite/retweet/reply counts pulled from raw_json for X signals."""
    from prism.ctr.features import extract

    conn = _mkconn()
    tweet_json = json.dumps({
        "tweet": {"favorite_count": 42, "retweet_count": 7, "reply_count": 3}
    })
    _seed(conn, sid=1, stype="x", raw_json=tweet_json)
    conn.commit()
    feats = extract(conn, 1)
    assert feats["eng_likes"] == 42.0
    assert feats["eng_retweets"] == 7.0
    assert feats["eng_replies"] == 3.0


def test_extract_missing_signal_returns_zeros():
    from prism.ctr.features import FEATURE_NAMES, extract

    conn = _mkconn()
    feats = extract(conn, 9999)
    assert set(feats.keys()) == set(FEATURE_NAMES)
    assert all(v == 0.0 for v in feats.values())
