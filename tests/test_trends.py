from prism.pipeline.trends import calculate_trends


def test_calculate_trends_new_topic(db):
    # Insert today's cluster with signal
    db.execute("INSERT INTO clusters (id, date, topic_label, item_count) VALUES (1, '2026-03-24', 'vLLM', 3)")
    db.execute("INSERT INTO signals (cluster_id, signal_strength, is_current, analysis_type) VALUES (1, 4, 1, 'daily')")
    db.commit()
    calculate_trends(db, date="2026-03-24")
    trend = db.execute("SELECT * FROM trends WHERE topic_label='vLLM' AND date='2026-03-24'").fetchone()
    assert trend is not None
    assert trend["heat_score"] > 0


def test_calculate_trends_heating(db):
    # Yesterday's trend
    db.execute("INSERT INTO trends (topic_label, date, heat_score, is_current) VALUES ('vLLM', '2026-03-23', 5.0, 1)")
    # Today: higher
    db.execute("INSERT INTO clusters (id, date, topic_label, item_count) VALUES (1, '2026-03-24', 'vLLM', 5)")
    db.execute("INSERT INTO signals (cluster_id, signal_strength, is_current, analysis_type) VALUES (1, 5, 1, 'daily')")
    db.commit()
    calculate_trends(db, date="2026-03-24")
    trend = db.execute("SELECT * FROM trends WHERE topic_label='vLLM' AND date='2026-03-24'").fetchone()
    assert trend["delta_vs_yesterday"] > 0
