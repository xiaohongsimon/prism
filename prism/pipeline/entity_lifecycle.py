"""
Entity lifecycle scoring for Prism v2 Entity Core.

Computes exponential-decay momentum scores (m7, m30) and applies
rule-based status transitions (emerging → growing → mature → declining).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta
from math import exp
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IMPACT_WEIGHT: dict[str, float] = {
    "high": 3.0,
    "medium": 1.5,
    "low": 0.5,
}
PRACTICE_BOOST: float = 1.25


# ---------------------------------------------------------------------------
# update_lifecycle_scores
# ---------------------------------------------------------------------------

def update_lifecycle_scores(conn: sqlite3.Connection, today_str: str) -> int:
    """Recompute momentum scores for all non-archived entity_profiles.

    For each entity:
      - Queries entity_events from the past 60 days.
      - Computes m7 = Σ(weight * exp(-age/7)) and m30 = Σ(weight * exp(-age/30))
        where weight = IMPACT_WEIGHT[impact] * confidence * (PRACTICE_BOOST if
        event_type starts with "practice_").
      - Counts events in 7-day and 30-day windows plus total count.
      - Records last_event_at = MAX(event date).
      - UPDATEs entity_profiles with all computed values.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open database connection (row_factory=sqlite3.Row recommended).
    today_str : str
        ISO date string for "today", e.g. "2026-03-29".

    Returns
    -------
    int
        Number of entity rows updated.
    """
    try:
        today = date.fromisoformat(today_str)
    except ValueError:
        logger.error("update_lifecycle_scores: invalid today_str %r", today_str)
        return 0

    window_start = today - timedelta(days=60)
    window_start_str = window_start.isoformat()

    # Fetch all non-archived entities (archived check: status column has no
    # 'archived' value in the current schema, so we fetch all)
    entities = conn.execute(
        "SELECT id FROM entity_profiles"
    ).fetchall()

    updated = 0
    for ent in entities:
        entity_id = ent["id"]

        # Fetch events within the 60-day window
        rows = conn.execute(
            """
            SELECT date, event_type, impact, confidence
            FROM entity_events
            WHERE entity_id = ? AND date >= ?
            ORDER BY date
            """,
            (entity_id, window_start_str),
        ).fetchall()

        m7 = 0.0
        m30 = 0.0
        count_7d = 0
        count_30d = 0
        last_event_date: Optional[str] = None

        for row in rows:
            event_date_str = row["date"]
            try:
                event_date = date.fromisoformat(event_date_str[:10])
            except (ValueError, TypeError):
                continue

            age = (today - event_date).days
            if age < 0:
                age = 0

            impact = row["impact"] or "medium"
            confidence = row["confidence"] if row["confidence"] is not None else 0.8
            event_type = row["event_type"] or ""

            base_weight = IMPACT_WEIGHT.get(impact, IMPACT_WEIGHT["medium"])
            weight = base_weight * confidence
            if event_type.startswith("practice_"):
                weight *= PRACTICE_BOOST

            m7 += weight * exp(-age / 7)
            m30 += weight * exp(-age / 30)

            if age <= 7:
                count_7d += 1
            if age <= 30:
                count_30d += 1

            # Track the most recent event date string
            if last_event_date is None or event_date_str > last_event_date:
                last_event_date = event_date_str

        # Also count total events (all time)
        total_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM entity_events WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
        count_total = total_row["cnt"] if total_row else 0

        conn.execute(
            """
            UPDATE entity_profiles
            SET m7_score         = ?,
                m30_score        = ?,
                event_count_7d   = ?,
                event_count_30d  = ?,
                event_count_total = ?,
                last_event_at    = ?
            WHERE id = ?
            """,
            (m7, m30, count_7d, count_30d, count_total, last_event_date, entity_id),
        )
        updated += 1

    conn.commit()
    logger.info("update_lifecycle_scores: updated %d entities", updated)
    return updated


# ---------------------------------------------------------------------------
# compute_status
# ---------------------------------------------------------------------------

def compute_status(entity_row: sqlite3.Row) -> str:
    """Derive lifecycle status from an entity_profiles row.

    Pure function — no DB access.  Rules applied in priority order:
      emerging  : age ≤ 14d AND event_count_total ≥ 2 AND m7 ≥ 3
      growing   : event_count_total ≥ 4 AND m7 ≥ 1.5 × baseline
      mature    : age ≥ 21d AND m30 ≥ 8 AND 0.67 ≤ m7/baseline ≤ 1.5
      declining : age ≥ 21d AND (days_silent > 14 OR m7 < 0.5 × baseline)
      else      : keep current status

    Parameters
    ----------
    entity_row : sqlite3.Row
        A row from entity_profiles (row_factory=sqlite3.Row).

    Returns
    -------
    str
        One of "emerging", "growing", "mature", "declining", or the
        existing status value.
    """
    # Parse first_seen_at
    first_seen_str = entity_row["first_seen_at"] or ""
    try:
        first_seen = datetime.strptime(first_seen_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        first_seen = date.today()

    today = date.today()
    age_days = (today - first_seen).days

    # Momentum scores
    m7: float = entity_row["m7_score"] or 0.0
    m30: float = entity_row["m30_score"] or 0.0

    # Baseline = max(1.0, m30 / 4.3)
    baseline: float = max(1.0, m30 / 4.3)

    # Event counts
    event_count_total: int = entity_row["event_count_total"] or 0

    # days_silent: days since last_event_at (None → very large)
    last_event_str = entity_row["last_event_at"]
    if last_event_str:
        try:
            last_event = datetime.strptime(last_event_str[:10], "%Y-%m-%d").date()
            days_silent = (today - last_event).days
        except (ValueError, TypeError):
            days_silent = 999
    else:
        days_silent = 999

    current_status: str = entity_row["status"] or "emerging"

    # --- Rule evaluation (priority order) ---

    # emerging: young AND enough events AND active momentum
    if age_days <= 14 and event_count_total >= 2 and m7 >= 3.0:
        return "emerging"

    # growing: sufficient history AND strong relative momentum
    if event_count_total >= 4 and m7 >= 1.5 * baseline:
        return "growing"

    # mature: established AND stable momentum band
    if age_days >= 21 and m30 >= 8.0:
        ratio = m7 / baseline if baseline > 0 else 0.0
        if 0.67 <= ratio <= 1.5:
            return "mature"

    # declining: established AND silent OR weak momentum
    if age_days >= 21 and (days_silent > 14 or m7 < 0.5 * baseline):
        return "declining"

    return current_status


# ---------------------------------------------------------------------------
# update_entity_statuses
# ---------------------------------------------------------------------------

def update_entity_statuses(conn: sqlite3.Connection) -> int:
    """Apply compute_status to every non-archived entity and persist changes.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open database connection.

    Returns
    -------
    int
        Number of rows whose status changed.
    """
    entities = conn.execute(
        "SELECT * FROM entity_profiles"
    ).fetchall()

    changed = 0
    for entity in entities:
        new_status = compute_status(entity)
        if new_status != entity["status"]:
            conn.execute(
                "UPDATE entity_profiles SET status = ? WHERE id = ?",
                (new_status, entity["id"]),
            )
            changed += 1

    if changed:
        conn.commit()

    logger.info("update_entity_statuses: %d status changes", changed)
    return changed
