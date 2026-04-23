import sqlite3

from prism.db import init_db


def _seed(conn):
    conn.execute("INSERT INTO clusters (id, date, topic_label) VALUES (1, '2026-04-19', 'AI')")
    conn.execute(
        "INSERT INTO signals (id, cluster_id, summary, signal_layer, signal_strength, "
        "tags_json, is_current, analysis_type) "
        "VALUES (1, 1, 's1', 'actionable', 4, '[\"llm\",\"eval\"]', 1, 'daily')"
    )
    conn.execute(
        "INSERT INTO sources (id, source_key, type, handle) VALUES (1, 'x:karpathy', 'x', 'karpathy')"
    )
    conn.execute(
        "INSERT INTO raw_items (id, source_id, url, author, body) "
        "VALUES (1, 1, 'https://x/1', 'karpathy', 'text')"
    )
    conn.execute("INSERT INTO cluster_items (cluster_id, raw_item_id) VALUES (1, 1)")
    conn.commit()


def test_save_writes_event_and_updates_preference_weights():
    from prism.web.feed import record_feed_action

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    _seed(conn)

    record_feed_action(conn, signal_id=1, action="save", target_key="", response_time_ms=0)

    ev = conn.execute(
        "SELECT signal_id, action FROM feed_interactions"
    ).fetchone()
    assert ev["signal_id"] == 1 and ev["action"] == "save"

    # save bumps all dimensions by +2.0. Post Wave 1 there is no
    # signal_scores / bt_score update — preference_weights is the only
    # learning signal.
    author_w = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='author' AND key='karpathy'"
    ).fetchone()
    assert author_w["weight"] == 2.0

    tag_w = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='tag' AND key='llm'"
    ).fetchone()
    assert tag_w["weight"] == 2.0


def test_dismiss_drops_bt_and_weights():
    from prism.web.feed import record_feed_action

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    _seed(conn)

    record_feed_action(conn, signal_id=1, action="dismiss", target_key="", response_time_ms=0)

    ev = conn.execute("SELECT action FROM feed_interactions").fetchone()
    assert ev["action"] == "dismiss"

    author_w = conn.execute(
        "SELECT weight FROM preference_weights WHERE dimension='author' AND key='karpathy'"
    ).fetchone()
    assert author_w["weight"] == -1.0
