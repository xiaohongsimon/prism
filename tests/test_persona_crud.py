import json
import sqlite3

from prism.db import init_db
from prism.persona import save_snapshot, load_active_snapshot


def _mkconn():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    return conn


def test_save_snapshot_returns_id_and_activates():
    conn = _mkconn()
    snap_id = save_snapshot(
        conn,
        answers={"role": "TL", "goals": ["方法论"]},
        free_text="想做一个会学习的推荐系统",
        seed_handles=["zarazhangrui", "danshipper"],
    )
    assert isinstance(snap_id, int)

    active = load_active_snapshot(conn)
    assert active is not None
    assert active["id"] == snap_id
    assert json.loads(active["answers_json"]) == {"role": "TL", "goals": ["方法论"]}
    assert active["free_text"] == "想做一个会学习的推荐系统"
    assert json.loads(active["seed_handles_json"]) == ["zarazhangrui", "danshipper"]


def test_new_snapshot_deactivates_previous():
    conn = _mkconn()
    first = save_snapshot(conn, answers={"a": 1}, free_text="", seed_handles=[])
    second = save_snapshot(conn, answers={"a": 2}, free_text="", seed_handles=[])
    assert first != second

    active = load_active_snapshot(conn)
    assert active["id"] == second

    row = conn.execute(
        "SELECT is_active FROM persona_snapshots WHERE id = ?", (first,)
    ).fetchone()
    assert row[0] == 0


def test_load_active_returns_none_when_empty():
    conn = _mkconn()
    assert load_active_snapshot(conn) is None
