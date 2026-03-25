"""API route handlers."""

import json
import sqlite3
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter()


def _db(request: Request) -> sqlite3.Connection:
    return request.state.db


@router.get("/signals")
def get_signals(request: Request, days: int = 7, layer: Optional[str] = None,
                topic: Optional[str] = None, limit: int = 50):
    """Query current signals."""
    conn = _db(request)
    query = (
        "SELECT s.*, c.topic_label, c.date FROM signals s "
        "JOIN clusters c ON s.cluster_id = c.id "
        "WHERE s.is_current = 1"
    )
    params: list = []

    if layer:
        query += " AND s.signal_layer = ?"
        params.append(layer)
    if topic:
        query += " AND c.topic_label LIKE ?"
        params.append(f"%{topic}%")

    query += f" AND c.date >= date('now', '-{int(days)} days')"
    query += " ORDER BY s.signal_strength DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


@router.get("/trends")
def get_trends(request: Request, days: int = 7, topic: Optional[str] = None):
    """Query trend data."""
    conn = _db(request)
    query = "SELECT * FROM trends WHERE is_current = 1"
    params: list = []

    if topic:
        query += " AND topic_label LIKE ?"
        params.append(f"%{topic}%")

    query += f" AND date >= date('now', '-{int(days)} days')"
    query += " ORDER BY heat_score DESC"

    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


@router.get("/clusters/{cluster_id}")
def get_cluster(request: Request, cluster_id: int):
    """Get cluster detail with items."""
    conn = _db(request)
    cluster = conn.execute("SELECT * FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
    if not cluster:
        return JSONResponse(status_code=404, content={"error": "Cluster not found"})

    items = conn.execute(
        "SELECT ri.* FROM raw_items ri "
        "JOIN cluster_items ci ON ri.id = ci.raw_item_id "
        "WHERE ci.cluster_id = ?", (cluster_id,)
    ).fetchall()

    signals = conn.execute(
        "SELECT * FROM signals WHERE cluster_id = ? AND is_current = 1",
        (cluster_id,),
    ).fetchall()

    return {
        "cluster": dict(cluster),
        "items": [dict(i) for i in items],
        "signals": [dict(s) for s in signals],
    }


@router.get("/briefing")
def get_briefing(request: Request, date: Optional[str] = None):
    """Get briefing for a date."""
    conn = _db(request)
    if date:
        row = conn.execute("SELECT * FROM briefings WHERE date = ?", (date,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM briefings ORDER BY date DESC LIMIT 1").fetchone()

    if not row:
        return JSONResponse(status_code=404, content={"error": "Briefing not found"})
    return dict(row)


@router.get("/search")
def search(request: Request, q: str = Query(..., min_length=1)):
    """Full-text search across raw items."""
    conn = _db(request)
    rows = conn.execute(
        "SELECT ri.* FROM raw_items ri "
        "JOIN item_search ON item_search.rowid = ri.id "
        "WHERE item_search MATCH ? LIMIT 20",
        (q,),
    ).fetchall()
    return [dict(r) for r in rows]


@router.post("/sources", status_code=201)
def add_source(request: Request, body: dict):
    """Add a new source."""
    conn = _db(request)
    source_type = body.get("type", "")
    handle = body.get("handle", "")
    source_key = f"{source_type}:{handle}"

    conn.execute(
        "INSERT OR IGNORE INTO sources (source_key, type, handle, enabled, origin) "
        "VALUES (?, ?, ?, 1, 'api')",
        (source_key, source_type, handle),
    )
    conn.commit()
    return {"source_key": source_key, "status": "created"}


@router.put("/sources/{source_key}")
def update_source(request: Request, source_key: str, body: dict):
    """Update source settings."""
    conn = _db(request)
    if "enabled" in body:
        conn.execute(
            "UPDATE sources SET enabled = ? WHERE source_key = ?",
            (int(body["enabled"]), source_key),
        )
        conn.commit()
    row = conn.execute("SELECT * FROM sources WHERE source_key = ?", (source_key,)).fetchone()
    if not row:
        return JSONResponse(status_code=404, content={"error": "Source not found"})
    return dict(row)


@router.delete("/sources/{source_key}")
def delete_source(request: Request, source_key: str):
    """Disable a source."""
    conn = _db(request)
    conn.execute(
        "UPDATE sources SET enabled = 0, disabled_reason = 'manual' WHERE source_key = ?",
        (source_key,),
    )
    conn.commit()
    return {"source_key": source_key, "status": "disabled"}
