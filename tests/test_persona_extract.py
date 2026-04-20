import json
import sqlite3
from unittest.mock import patch

from prism.db import init_db
from prism.persona import save_snapshot, extract_from_snapshot


FAKE_LLM_OUTPUT = {
    "summary": "TL 想积累方法论和个人成长内容",
    "bias_weights": [
        {"dimension": "tag", "key": "方法论", "weight": 3.0},
        {"dimension": "tag", "key": "LLM", "weight": -2.0},
        {"dimension": "layer", "key": "strategic", "weight": -2.0},
        # weight outside clip range should be clamped
        {"dimension": "tag", "key": "个人成长", "weight": 99.0},
    ],
    "candidate_sources": [
        {
            "type": "x",
            "handle": "zarazhangrui",
            "display_name": "Zara Zhang Rui",
            "depth": "thread",
            "rationale": "user listed as seed; methodology-oriented",
            "category": "growth-methodology",
        },
        {
            "type": "rss",
            "url": "https://example.com/newsletter.xml",
            "display_name": "Example Weekly",
            "rationale": "adjacent to user's stated interests",
            "category": "newsletter",
        },
    ],
}


def _mkconn():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    return conn


def test_extract_writes_bias_weights_and_proposals():
    conn = _mkconn()
    snap_id = save_snapshot(
        conn,
        answers={"role": "TL", "goals": ["积累方法论"]},
        free_text="",
        seed_handles=["zarazhangrui"],
    )

    with patch("prism.persona.call_llm_json", return_value=FAKE_LLM_OUTPUT) as mock_llm:
        n_weights, n_proposals = extract_from_snapshot(conn, snap_id)

    assert mock_llm.call_count == 1
    assert n_weights == 4
    assert n_proposals == 2

    # preference_weights written with dimension='persona_bias'
    rows = conn.execute(
        "SELECT dimension, key, weight FROM preference_weights "
        "WHERE dimension = 'persona_bias'"
    ).fetchall()
    weight_by_key = {f"{d}:{k}": w for d, k, w in rows}
    # clipped at ±5
    assert weight_by_key["persona_bias:tag/个人成长"] == 5.0
    assert weight_by_key["persona_bias:tag/方法论"] == 3.0
    assert weight_by_key["persona_bias:tag/LLM"] == -2.0

    # source_proposals written with origin='persona'
    proposals = conn.execute(
        "SELECT origin, source_type, display_name FROM source_proposals"
    ).fetchall()
    assert len(proposals) == 2
    assert all(p[0] == "persona" for p in proposals)
    assert {p[2] for p in proposals} == {"Zara Zhang Rui", "Example Weekly"}

    # extracted_summary persisted on snapshot
    summary = conn.execute(
        "SELECT extracted_summary FROM persona_snapshots WHERE id = ?", (snap_id,)
    ).fetchone()[0]
    assert "TL" in summary


def test_extract_previous_persona_bias_is_zeroed_on_new_snapshot():
    conn = _mkconn()
    s1 = save_snapshot(conn, answers={"role": "v1"}, free_text="", seed_handles=[])
    with patch("prism.persona.call_llm_json", return_value=FAKE_LLM_OUTPUT):
        extract_from_snapshot(conn, s1)

    # New snapshot supersedes
    s2 = save_snapshot(conn, answers={"role": "v2"}, free_text="", seed_handles=[])
    with patch(
        "prism.persona.call_llm_json",
        return_value={
            "summary": "v2",
            "bias_weights": [{"dimension": "tag", "key": "其他", "weight": 2.0}],
            "candidate_sources": [],
        },
    ):
        extract_from_snapshot(conn, s2)

    # Old bias keys should be zero; new bias key present
    rows = dict(conn.execute(
        "SELECT key, weight FROM preference_weights WHERE dimension = 'persona_bias'"
    ).fetchall())
    # Old keys from FAKE_LLM_OUTPUT should be 0.0 (not deleted)
    assert rows.get("tag/方法论") == 0.0
    assert rows.get("tag/其他") == 2.0
