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


# ── Channel diversity (打散) ──────────────────────────────────────────

def test_diversify_by_channel_caps_consecutive_same_type():
    from prism.web.feed import _diversify_by_channel
    # 15 signals, 3 types × 5 each, score-ordered by type. Pure-score
    # would produce YYYYYXXXXXHHHHH. With 3 types the window=5 / cap=2
    # invariant IS mathematically achievable (ceil(5/2)=3 types needed).
    ranked = (
        [{"signal_id": i, "source_types": ["youtube"]} for i in range(1, 6)]
        + [{"signal_id": i + 10, "source_types": ["x"]} for i in range(1, 6)]
        + [{"signal_id": i + 20, "source_types": ["hn"]} for i in range(1, 6)]
    )
    out = _diversify_by_channel(ranked, window=5, max_per_type=2)
    for start in range(len(out) - 4):
        win = out[start:start + 5]
        counts: dict = {}
        for s in win:
            t = s["source_types"][0]
            counts[t] = counts.get(t, 0) + 1
        assert max(counts.values()) <= 2, f"window {start} violates cap: {counts}"
    assert {s["signal_id"] for s in out} == {s["signal_id"] for s in ranked}


def test_diversify_degrades_gracefully_when_types_too_few():
    """With only 2 types, window=5 / cap=2 is impossible (ceil(5/2)=3
    types needed). Algorithm must still return all items, not infinite-loop,
    and keep runs to at most 3 of same type in any 5-window."""
    from prism.web.feed import _diversify_by_channel
    ranked = (
        [{"signal_id": i, "source_types": ["youtube"]} for i in range(1, 6)]
        + [{"signal_id": i + 10, "source_types": ["x"]} for i in range(1, 6)]
    )
    out = _diversify_by_channel(ranked, window=5, max_per_type=2)
    assert len(out) == 10
    assert {s["signal_id"] for s in out} == {s["signal_id"] for s in ranked}
    for start in range(len(out) - 4):
        types = [s["source_types"][0] for s in out[start:start + 5]]
        assert max(types.count(t) for t in set(types)) <= 3


def test_diversify_preserves_score_order_for_top_pick():
    from prism.web.feed import _diversify_by_channel
    ranked = [
        {"signal_id": 1, "source_types": ["youtube"]},
        {"signal_id": 2, "source_types": ["x"]},
        {"signal_id": 3, "source_types": ["hn"]},
    ]
    out = _diversify_by_channel(ranked, window=5, max_per_type=2)
    # Already diverse — order should be preserved.
    assert [s["signal_id"] for s in out] == [1, 2, 3]


def test_diversify_falls_back_when_pool_is_monochrome():
    """If the pool is dominated by one type, we still return everything."""
    from prism.web.feed import _diversify_by_channel
    ranked = [{"signal_id": i, "source_types": ["youtube"]} for i in range(5)]
    out = _diversify_by_channel(ranked, window=5, max_per_type=2)
    assert len(out) == 5
    assert {s["signal_id"] for s in out} == {0, 1, 2, 3, 4}


def test_rank_feed_interleaves_channels_end_to_end():
    """Top 6 slots of /feed shouldn't be all-YouTube even when YouTube
    signals have the top raw scores. Uses 3 types so the window=5/cap=2
    invariant is achievable."""
    import json
    from prism.web.feed import rank_feed
    conn = _mkconn()

    src_specs = [(1, "yt:demo", "youtube"), (2, "x:demo", "x"), (3, "hn:demo", "hn")]
    for sid, sk, stype in src_specs:
        conn.execute(
            "INSERT INTO sources (id, source_key, type, handle) VALUES (?, ?, ?, 'demo')",
            (sid, sk, stype),
        )

    # 5 youtube (strength=5), 5 x (strength=3), 5 hn (strength=3)
    for i in range(1, 16):
        src_id = 1 if i <= 5 else (2 if i <= 10 else 3)
        strength = 5 if i <= 5 else 3
        conn.execute(
            "INSERT INTO clusters (id, date, topic_label) VALUES (?, '2026-04-19', 'AI')",
            (i,),
        )
        conn.execute(
            "INSERT INTO signals (id, cluster_id, summary, signal_layer, signal_strength, "
            "tags_json, is_current, analysis_type) VALUES (?, ?, ?, 'actionable', ?, ?, 1, 'daily')",
            (i, i, f"s{i}", strength, json.dumps(["ai"])),
        )
        conn.execute(
            "INSERT INTO raw_items (id, source_id, url, author, body) VALUES (?, ?, ?, ?, 't')",
            (i, src_id, f"https://demo/{i}", f"a{i}"),
        )
        conn.execute(
            "INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (?, ?)", (i, i)
        )
    conn.commit()

    rows = rank_feed(conn, limit=6, offset=0)
    types = [(r.get("source_types") or [""])[0] for r in rows]
    assert types.count("youtube") <= 2, (
        f"top-6 has too many youtube: {types}"
    )
    # Top result should still be the highest-score type (youtube).
    assert types[0] == "youtube"
