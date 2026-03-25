"""Trend calculation: heat score and day-over-day delta."""

import json
import logging
import sqlite3
from datetime import datetime, timedelta

from prism.db import insert_job_run, finish_job_run

logger = logging.getLogger(__name__)


def calculate_trends(conn: sqlite3.Connection, date: str) -> int:
    """Calculate trends for clusters on a given date.

    heat_score = signal_strength * item_count
    delta_vs_yesterday = today's heat - yesterday's heat for same topic

    Returns count of trends created.
    """
    yesterday = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")

    # Invalidate previous trend entries for this date
    conn.execute(
        "UPDATE trends SET is_current = 0 WHERE date = ? AND is_current = 1",
        (date,),
    )

    # Get clusters with their daily signals for this date
    rows = conn.execute(
        "SELECT c.id, c.topic_label, c.item_count, s.signal_strength "
        "FROM clusters c "
        "JOIN signals s ON c.id = s.cluster_id "
        "WHERE c.date = ? AND s.is_current = 1 AND s.analysis_type = 'daily'",
        (date,),
    ).fetchall()

    if not rows:
        return 0

    # Get yesterday's trends for delta calculation
    yesterday_trends = {}
    for t in conn.execute(
        "SELECT topic_label, heat_score FROM trends WHERE date = ? AND is_current = 1",
        (yesterday,),
    ).fetchall():
        yesterday_trends[t["topic_label"]] = t["heat_score"]

    job_id = insert_job_run(conn, job_type="trends")
    count = 0

    for row in rows:
        heat_score = row["signal_strength"] * row["item_count"]
        yesterday_heat = yesterday_trends.get(row["topic_label"], 0.0)
        delta = heat_score - yesterday_heat

        conn.execute(
            "INSERT INTO trends (topic_label, date, heat_score, delta_vs_yesterday, job_run_id, is_current) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (row["topic_label"], date, heat_score, delta, job_id),
        )
        count += 1

    conn.commit()
    finish_job_run(conn, job_id, status="ok", stats_json=json.dumps({"trends_created": count}))
    return count
