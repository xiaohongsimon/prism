import sqlite3

from prism.db import init_db


def _seed_signal(conn, sid, author, tags, source_key="x:demo"):
    import json
    conn.execute(
        "INSERT OR IGNORE INTO sources (id, source_key, type, handle) "
        "VALUES (1, ?, 'x', 'demo')",
        (source_key,),
    )
    # Each signal gets its own cluster so authors don't bleed across signals.
    conn.execute(
        "INSERT OR IGNORE INTO clusters (id, date, topic_label) "
        "VALUES (?, '2026-04-19', 'AI')",
        (sid,),
    )
    conn.execute(
        "INSERT INTO signals (id, cluster_id, summary, signal_layer, signal_strength, "
        "tags_json, is_current, analysis_type) "
        "VALUES (?, ?, ?, 'actionable', 3, ?, 1, 'daily')",
        (sid, sid, f"s{sid}", json.dumps(tags)),
    )
    conn.execute(
        "INSERT INTO raw_items (id, source_id, url, author, body) "
        "VALUES (?, 1, ?, ?, 't')",
        (sid, f"https://x/{sid}", author),
    )
    conn.execute(
        "INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (?, ?)",
        (sid, sid),
    )


def _mkconn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_rank_feed_returns_signals_highest_first():
    from prism.web.feed import rank_feed
    conn = _mkconn()
    for i in range(1, 6):
        _seed_signal(conn, i, f"a{i}", ["ai"])
    conn.commit()

    # Boost signal 3 via preference_weights (author match)
    conn.execute(
        "INSERT INTO preference_weights (dimension, key, weight) VALUES ('author','a3', 5.0)"
    )
    conn.commit()

    rows = rank_feed(conn, limit=5, offset=0)
    assert rows, "should return something"
    assert rows[0]["signal_id"] == 3


def test_rank_feed_excludes_recently_dismissed():
    from prism.web.feed import rank_feed, record_feed_action
    conn = _mkconn()
    for i in range(1, 4):
        _seed_signal(conn, i, f"a{i}", ["ai"])
    conn.commit()

    record_feed_action(conn, signal_id=2, action="dismiss")

    rows = rank_feed(conn, limit=10, offset=0)
    ids = [r["signal_id"] for r in rows]
    assert 2 not in ids


def test_rank_feed_pagination():
    """Pagination: two pages with limit=2 must not overlap.

    Uses mixed source types (x, hn, arxiv) so the diversity cap in
    _get_candidate_pool doesn't randomly drop signals from the small pool.
    """
    import json
    from prism.web.feed import rank_feed

    conn = _mkconn()

    # Insert three distinct source types so no single type dominates.
    types = [("x:demo", "x"), ("hn:demo", "hn"), ("arxiv:demo", "arxiv")]
    for src_id, (sk, stype) in enumerate(types, start=1):
        conn.execute(
            "INSERT OR IGNORE INTO sources (id, source_key, type, handle) VALUES (?, ?, ?, 'demo')",
            (src_id, sk, stype),
        )

    # 6 signals, 2 per source type
    for i in range(1, 7):
        src_id = ((i - 1) % 3) + 1
        source_key = types[src_id - 1][0]
        conn.execute(
            "INSERT OR IGNORE INTO clusters (id, date, topic_label) VALUES (?, '2026-04-19', 'AI')",
            (i,),
        )
        conn.execute(
            "INSERT INTO signals (id, cluster_id, summary, signal_layer, signal_strength, "
            "tags_json, is_current, analysis_type) VALUES (?, ?, ?, 'actionable', 3, ?, 1, 'daily')",
            (i, i, f"s{i}", json.dumps(["ai"])),
        )
        conn.execute(
            "INSERT INTO raw_items (id, source_id, url, author, body) VALUES (?, ?, ?, ?, 't')",
            (i, src_id, f"https://example/{i}", f"a{i}"),
        )
        conn.execute(
            "INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (?, ?)",
            (i, i),
        )

    conn.commit()

    page1 = rank_feed(conn, limit=2, offset=0)
    page2 = rank_feed(conn, limit=2, offset=2)
    ids1 = {r["signal_id"] for r in page1}
    ids2 = {r["signal_id"] for r in page2}
    assert ids1.isdisjoint(ids2)
