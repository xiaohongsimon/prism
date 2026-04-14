from unittest.mock import patch, MagicMock
from prism.pipeline.analyze import run_incremental_analysis, run_daily_analysis


def test_incremental_analysis_stores_signal(db):
    # Insert a cluster with no signal yet
    db.execute("INSERT INTO clusters (id, date, topic_label, item_count, merged_context) VALUES (1, '2026-03-24', 'test', 1, 'test context')")
    db.commit()
    mock_response = {
        "summary": "测试摘要", "signal_layer": "strategic", "signal_strength": 3,
        "why_it_matters": "测试原因", "action": "无", "tl_perspective": "测试视角",
        "tags": ["test"]
    }
    with patch("prism.pipeline.analyze.call_llm_json", return_value=mock_response):
        run_incremental_analysis(db, model="test-model")
    signal = db.execute("SELECT * FROM signals WHERE cluster_id = 1 AND is_current = 1").fetchone()
    assert signal is not None
    assert signal["analysis_type"] == "incremental"
    assert signal["signal_layer"] == "strategic"


def test_daily_analysis_generates_narrative(db):
    # Insert cluster + incremental signal for narrative generation
    db.execute("INSERT INTO clusters (id, date, topic_label, item_count, merged_context) VALUES (1, '2026-03-24', 'test', 1, 'ctx')")
    db.execute("INSERT INTO signals (cluster_id, summary, analysis_type, is_current, signal_layer, signal_strength, why_it_matters) "
               "VALUES (1, 'test summary', 'incremental', 1, 'actionable', 4, 'important')")
    db.commit()
    with patch("prism.pipeline.analyze.call_llm", return_value="Today's narrative."):
        stats = run_daily_analysis(db, date="2026-03-24", model="test-model")
    assert stats["briefing_narrative"] == "Today's narrative."
    # Job run should be recorded
    job = db.execute("SELECT * FROM job_runs WHERE job_type = 'analyze_daily' ORDER BY id DESC LIMIT 1").fetchone()
    assert job["status"] == "ok"
