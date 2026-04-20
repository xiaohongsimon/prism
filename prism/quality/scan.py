"""Combined snapshot + rule evaluation — the watchdog's main entry."""
from __future__ import annotations

import sqlite3

from prism.quality.rules import evaluate
from prism.quality.snapshot import capture


def scan(conn: sqlite3.Connection) -> dict[str, int]:
    """Capture fresh metrics, then evaluate all rules.

    Returns counts so callers (CLI, cron) can log the run.
    """
    n_metrics = capture(conn)
    n_rules = evaluate(conn)
    return {"metrics": n_metrics, "rules": n_rules}
