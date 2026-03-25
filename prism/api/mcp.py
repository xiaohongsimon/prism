"""Minimal MCP (Model Context Protocol) server for Prism.

Exposes tools: query_signals, get_briefing, search_signals, signal_stats.
"""

import json
import sqlite3
from datetime import date
from pathlib import Path

from prism.config import settings
from prism.db import get_connection


def _get_conn() -> sqlite3.Connection:
    return get_connection(settings.db_path)


def query_signals(layer: str = "", days: int = 7, limit: int = 20) -> list[dict]:
    """Query current signals, optionally filtered by layer."""
    conn = _get_conn()
    query = (
        "SELECT s.*, c.topic_label, c.date FROM signals s "
        "JOIN clusters c ON s.cluster_id = c.id "
        "WHERE s.is_current = 1"
    )
    params: list = []
    if layer:
        query += " AND s.signal_layer = ?"
        params.append(layer)
    query += f" AND c.date >= date('now', '-{int(days)} days')"
    query += " ORDER BY s.signal_strength DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_briefing(dt: str = "") -> dict:
    """Get the briefing for a date (defaults to today)."""
    conn = _get_conn()
    target = dt or date.today().isoformat()
    row = conn.execute("SELECT * FROM briefings WHERE date = ?", (target,)).fetchone()
    if row:
        return dict(row)
    return {"error": f"No briefing for {target}"}


def search_signals(query: str) -> list[dict]:
    """Full-text search across raw items."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT ri.* FROM raw_items ri "
        "JOIN item_search ON item_search.rowid = ri.id "
        "WHERE item_search MATCH ? LIMIT 20",
        (query,),
    ).fetchall()
    return [dict(r) for r in rows]


def signal_stats(days: int = 7) -> dict:
    """Summary statistics for recent signals."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT signal_layer, COUNT(*) as cnt FROM signals s "
        "JOIN clusters c ON s.cluster_id = c.id "
        f"WHERE s.is_current = 1 AND c.date >= date('now', '-{int(days)} days') "
        "GROUP BY signal_layer",
    ).fetchall()
    stats = {r["signal_layer"]: r["cnt"] for r in rows}
    stats["total"] = sum(stats.values())
    return stats


# MCP tool definitions for registration
MCP_TOOLS = [
    {
        "name": "query_signals",
        "description": "Query current Prism signals by layer and time range",
        "parameters": {
            "layer": {"type": "string", "description": "Signal layer: actionable, strategic, noise"},
            "days": {"type": "integer", "description": "Number of days to look back", "default": 7},
            "limit": {"type": "integer", "description": "Max results", "default": 20},
        },
        "handler": query_signals,
    },
    {
        "name": "get_briefing",
        "description": "Get the daily briefing for a specific date",
        "parameters": {
            "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
        },
        "handler": get_briefing,
    },
    {
        "name": "search_signals",
        "description": "Full-text search across collected items",
        "parameters": {
            "query": {"type": "string", "description": "Search query"},
        },
        "handler": search_signals,
    },
    {
        "name": "signal_stats",
        "description": "Get summary statistics of recent signals",
        "parameters": {
            "days": {"type": "integer", "description": "Number of days", "default": 7},
        },
        "handler": signal_stats,
    },
]
