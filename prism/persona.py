"""Persona snapshot management + LLM extraction.

A persona snapshot is the user's self-description of who they are right now
and what they want Prism to surface. Snapshots are versioned; only one is
active at a time.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any


def save_snapshot(
    conn: sqlite3.Connection,
    answers: dict[str, Any],
    free_text: str = "",
    seed_handles: list[str] | None = None,
) -> int:
    """Save a persona snapshot. Deactivates any prior active snapshot.
    Returns the new snapshot id."""
    seed_handles = seed_handles or []
    conn.execute("UPDATE persona_snapshots SET is_active = 0 WHERE is_active = 1")
    cur = conn.execute(
        "INSERT INTO persona_snapshots (answers_json, free_text, seed_handles_json, is_active) "
        "VALUES (?, ?, ?, 1)",
        (json.dumps(answers, ensure_ascii=False),
         free_text,
         json.dumps(seed_handles, ensure_ascii=False)),
    )
    conn.commit()
    return cur.lastrowid


def load_active_snapshot(conn: sqlite3.Connection) -> dict | None:
    """Return the currently active persona snapshot, or None if none exists."""
    row = conn.execute(
        "SELECT id, answers_json, free_text, seed_handles_json, extracted_summary, "
        "       is_active, created_at "
        "FROM persona_snapshots WHERE is_active = 1 "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "answers_json": row[1],
        "free_text": row[2],
        "seed_handles_json": row[3],
        "extracted_summary": row[4],
        "is_active": row[5],
        "created_at": row[6],
    }
