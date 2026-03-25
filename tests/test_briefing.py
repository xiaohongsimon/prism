from prism.output.briefing import generate_briefing


def test_generate_briefing_produces_html_and_markdown(db):
    # Seed data: cluster + daily signal + trend + cross_link
    db.execute("INSERT INTO clusters (id, date, topic_label, item_count, merged_context) VALUES (1, '2026-03-24', 'vLLM', 2, 'ctx')")
    db.execute("""INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength,
        why_it_matters, action, tl_perspective, tags_json, analysis_type, is_current)
        VALUES (1, '测试摘要', 'actionable', 4, '重要', '评估', 'TL视角', '["vLLM"]', 'daily', 1)""")
    db.execute("INSERT INTO trends (topic_label, date, heat_score, delta_vs_yesterday, is_current) VALUES ('vLLM', '2026-03-24', 8.0, 3.0, 1)")
    db.commit()
    result = generate_briefing(db, date="2026-03-24")
    assert "<html" in result["html"].lower() or "<!doctype" in result["html"].lower()
    assert "# Prism Daily Brief" in result["markdown"]
    assert "vLLM" in result["markdown"]


def test_generate_briefing_stores_in_db(db):
    db.execute("INSERT INTO clusters (id, date, topic_label, item_count) VALUES (1, '2026-03-24', 'test', 1)")
    db.execute("INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, analysis_type, is_current) VALUES (1, 's', 'noise', 1, 'daily', 1)")
    db.commit()
    generate_briefing(db, date="2026-03-24", save=True)
    row = db.execute("SELECT * FROM briefings WHERE date = '2026-03-24'").fetchone()
    assert row is not None
