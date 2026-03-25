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


def test_daily_analysis_invalidates_incremental(db):
    # Insert cluster + incremental signal
    db.execute("INSERT INTO clusters (id, date, topic_label, item_count, merged_context) VALUES (1, '2026-03-24', 'test', 1, 'ctx')")
    db.execute("INSERT INTO signals (cluster_id, analysis_type, is_current, signal_layer) VALUES (1, 'incremental', 1, 'noise')")
    db.commit()
    mock_response = {
        "clusters": [{"cluster_id": 1, "summary": "s", "signal_layer": "actionable",
                       "signal_strength": 4, "why_it_matters": "w", "action": "a",
                       "tl_perspective": "t", "tags": ["x"]}],
        "cross_links": [], "trends": [],
        "briefing_narrative": "Today's brief."
    }
    with patch("prism.pipeline.analyze.call_llm_json", return_value=mock_response):
        run_daily_analysis(db, date="2026-03-24", model="test-model")
    # Incremental should be invalidated
    old = db.execute("SELECT * FROM signals WHERE cluster_id = 1 AND analysis_type = 'incremental'").fetchone()
    assert old["is_current"] == 0
    # Daily should be current
    new = db.execute("SELECT * FROM signals WHERE cluster_id = 1 AND analysis_type = 'daily' AND is_current = 1").fetchone()
    assert new["signal_layer"] == "actionable"
