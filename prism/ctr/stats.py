"""Bucket every signal as one of:

  - unseen        (曝光前): signal exists, never surfaced in feed_impressions
  - impressed     (曝光未互动): has impression, no click, no save
  - clicked       (曝光且点击): has impression + click, no save
  - saved         (曝光且收藏): has impression + save (click is implied/optional)

Timeline: a "signal" is considered in-scope if it was created (signals.created_at)
within the trailing N days. Impressions and interactions are filtered to the
same window — older data from previous experiments does not pollute the counts.

The classifier is write-anywhere: it can be called from CLI, tests, or a web
dashboard panel later without touching the DB state.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

BUCKETS = ("unseen", "impressed", "clicked", "saved")


@dataclass
class BucketCounts:
    unseen: int = 0
    impressed: int = 0
    clicked: int = 0
    saved: int = 0

    @property
    def total(self) -> int:
        return self.unseen + self.impressed + self.clicked + self.saved

    @property
    def shown(self) -> int:
        """Signals that were actually surfaced (non-unseen)."""
        return self.impressed + self.clicked + self.saved

    @property
    def ctr(self) -> float:
        """(clicked + saved) / shown. Save implies engagement — counts as CTR."""
        if self.shown == 0:
            return 0.0
        return (self.clicked + self.saved) / self.shown

    @property
    def save_rate(self) -> float:
        if self.shown == 0:
            return 0.0
        return self.saved / self.shown


@dataclass
class StatsReport:
    days: int
    overall: BucketCounts
    by_source_type: dict[str, BucketCounts] = field(default_factory=dict)


def classify(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
) -> StatsReport:
    """Return bucket counts overall and by source_type.

    Uses a single pass over signals in-window, joining each with its
    impression / click / save aggregates. SQL does the heavy lifting —
    no per-signal Python loops.
    """
    window_clause = f"datetime('now', '-{int(days)} days')"

    # Per-signal roll-up: has_impression, has_click, has_save, primary_type.
    # Clicks and saves are pulled from feed_interactions filtered to the
    # same window so ancient interactions don't fossilize a signal's bucket.
    # We pick ONE source_type per signal (the first one joined) for the
    # breakdown — most signals have a single type anyway.
    rows = conn.execute(
        f"""
        WITH in_window AS (
            SELECT s.id
            FROM signals s
            WHERE s.created_at > {window_clause}
        ),
        impr AS (
            SELECT DISTINCT signal_id
            FROM feed_impressions
            WHERE served_at > {window_clause}
        ),
        clicks AS (
            SELECT DISTINCT signal_id
            FROM feed_interactions
            WHERE action = 'click'
              AND created_at > {window_clause}
        ),
        saves AS (
            SELECT DISTINCT signal_id
            FROM feed_interactions
            WHERE action = 'save'
              AND created_at > {window_clause}
        ),
        primary_stype AS (
            SELECT s.id AS signal_id,
                   MIN(src.type) AS stype
            FROM signals s
            LEFT JOIN cluster_items ci ON ci.cluster_id = s.cluster_id
            LEFT JOIN raw_items ri     ON ri.id = ci.raw_item_id
            LEFT JOIN sources src      ON src.id = ri.source_id
            WHERE s.id IN (SELECT id FROM in_window)
            GROUP BY s.id
        )
        SELECT w.id AS signal_id,
               COALESCE(ps.stype, 'other') AS stype,
               CASE WHEN impr.signal_id   IS NOT NULL THEN 1 ELSE 0 END AS has_impr,
               CASE WHEN clicks.signal_id IS NOT NULL THEN 1 ELSE 0 END AS has_click,
               CASE WHEN saves.signal_id  IS NOT NULL THEN 1 ELSE 0 END AS has_save
        FROM in_window w
        LEFT JOIN primary_stype ps ON ps.signal_id = w.id
        LEFT JOIN impr   ON impr.signal_id   = w.id
        LEFT JOIN clicks ON clicks.signal_id = w.id
        LEFT JOIN saves  ON saves.signal_id  = w.id
        """
    ).fetchall()

    overall = BucketCounts()
    by_stype: dict[str, BucketCounts] = {}
    for r in rows:
        bucket = _bucket_from_flags(
            has_impr=bool(r["has_impr"]),
            has_click=bool(r["has_click"]),
            has_save=bool(r["has_save"]),
        )
        _inc(overall, bucket)
        stype = (r["stype"] or "other")
        _inc(by_stype.setdefault(stype, BucketCounts()), bucket)

    return StatsReport(days=days, overall=overall, by_source_type=by_stype)


def _bucket_from_flags(*, has_impr: bool, has_click: bool, has_save: bool) -> str:
    """Classification hierarchy: saved > clicked > impressed > unseen.

    Save dominates: a saved card is reported in the 'saved' bucket even
    if the user also clicked through. This matches how the CTR model
    treats labels — save is the canonical positive.
    """
    if has_save:
        return "saved"
    if has_click:
        # A click without an impression row means the impression
        # happened before feed_impressions was instrumented (or the
        # user hit the article page directly). Still counts as clicked.
        return "clicked"
    if has_impr:
        return "impressed"
    return "unseen"


def _inc(counts: BucketCounts, bucket: str) -> None:
    setattr(counts, bucket, getattr(counts, bucket) + 1)


def format_report(report: StatsReport) -> str:
    """Human-readable text block — used by `prism ctr stats`."""
    lines = [f"Window: last {report.days} days"]
    o = report.overall
    lines.append(
        f"Overall  total={o.total}  unseen={o.unseen}  "
        f"impressed={o.impressed}  clicked={o.clicked}  saved={o.saved}  "
        f"CTR={o.ctr:.3f}  save_rate={o.save_rate:.3f}"
    )
    if report.by_source_type:
        lines.append("")
        lines.append(
            f"{'source_type':<18}{'total':>8}{'unseen':>8}{'impr':>8}"
            f"{'click':>8}{'save':>8}{'CTR':>8}{'save%':>8}"
        )
        for st, c in sorted(
            report.by_source_type.items(),
            key=lambda kv: kv[1].total,
            reverse=True,
        ):
            lines.append(
                f"{st:<18}{c.total:>8}{c.unseen:>8}{c.impressed:>8}"
                f"{c.clicked:>8}{c.saved:>8}{c.ctr:>8.3f}{c.save_rate:>8.3f}"
            )
    return "\n".join(lines)
