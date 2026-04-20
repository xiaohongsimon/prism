"""Quality Watchdog — autonomous pipeline health monitoring.

The watchdog takes periodic snapshots of health metrics across the data
pipeline (ingest, cluster, analyze, feed interactions) and flags
deviations from historical baselines as `quality_anomalies`. The goal:
the user should never be the one who discovers a broken adapter, a
starved candidate pool, or a silent day of user inactivity.

Public entry points:
- snapshot.capture(conn): write one row per metric into quality_snapshots
- rules.evaluate(conn): read recent snapshots, open/update anomalies
- scan(conn): capture + evaluate, used by CLI and cron
"""
from prism.quality.snapshot import capture
from prism.quality.rules import evaluate
from prism.quality.scan import scan

__all__ = ["capture", "evaluate", "scan"]
