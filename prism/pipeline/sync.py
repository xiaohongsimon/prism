"""Sync pipeline: fetch from enabled sources, store items, track failures."""

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone

from prism.db import insert_raw_item, insert_job_run, finish_job_run
from prism.sources.base import SyncResult

logger = logging.getLogger(__name__)

# Failure thresholds
HARD_FAIL_THRESHOLD = 2
SOFT_FAIL_THRESHOLD = 6
HARD_FAIL_PATTERNS = ("403", "404", "not found", "forbidden")

# Per-source-type rate limiting (seconds between requests)
SOURCE_TYPE_DELAY = {"x": 3.0}


def get_adapter(source_type: str):
    """Return an adapter instance for the given source type."""
    from prism.sources import ADAPTERS

    # Handle legacy alias
    lookup = source_type if source_type != "github" else "github_trending"

    adapter_cls = ADAPTERS.get(lookup)
    if adapter_cls is None:
        raise ValueError(f"Unknown source type: {source_type}")
    return adapter_cls()


def _is_hard_failure(error: str) -> bool:
    """Classify error as hard (permanent) vs soft (transient)."""
    error_lower = error.lower()
    return any(p in error_lower for p in HARD_FAIL_PATTERNS)


def _get_syncable_sources(conn: sqlite3.Connection, source_key: str | None) -> list[sqlite3.Row]:
    """Get sources to sync: enabled + auto-retry candidates past their retry time."""
    if source_key:
        row = conn.execute("SELECT * FROM sources WHERE source_key = ?", (source_key,)).fetchone()
        return [row] if row else []

    # Enabled sources
    enabled = conn.execute("SELECT * FROM sources WHERE enabled = 1").fetchall()

    # Auto-retry candidates: disabled by auto, past retry time
    auto_retry = conn.execute(
        "SELECT * FROM sources WHERE enabled = 0 AND disabled_reason = 'auto' "
        "AND auto_retry_at IS NOT NULL AND auto_retry_at <= datetime('now')"
    ).fetchall()

    return list(enabled) + list(auto_retry)


def _handle_success(conn: sqlite3.Connection, source: sqlite3.Row, result: SyncResult) -> int:
    """Process a successful sync: store items, reset failures, re-enable if auto-retry."""
    stored = 0
    source_id = source["id"]

    for item in result.items:
        row_id = insert_raw_item(
            conn,
            source_id=source_id,
            url=item.url,
            title=item.title,
            body=item.body,
            author=item.author,
            published_at=item.published_at.isoformat() if item.published_at else "",
            raw_json=item.raw_json or "{}",
            thread_partial=item.thread_partial,
        )
        if row_id is not None:
            stored += 1

    # Reset failure tracking and re-enable if was auto-disabled
    conn.execute(
        "UPDATE sources SET consecutive_failures = 0, last_synced_at = datetime('now'), "
        "enabled = 1, disabled_reason = NULL, auto_retry_at = NULL "
        "WHERE id = ?",
        (source_id,),
    )
    conn.commit()
    return stored


def _handle_failure(conn: sqlite3.Connection, source: sqlite3.Row, result: SyncResult) -> None:
    """Process a failed sync: increment failures, possibly auto-disable."""
    source_id = source["id"]
    new_failures = source["consecutive_failures"] + 1
    hard = _is_hard_failure(result.error)
    threshold = HARD_FAIL_THRESHOLD if hard else SOFT_FAIL_THRESHOLD

    if new_failures >= threshold:
        conn.execute(
            "UPDATE sources SET consecutive_failures = ?, enabled = 0, "
            "disabled_reason = 'auto', auto_retry_at = datetime('now', '+24 hours') "
            "WHERE id = ?",
            (new_failures, source_id),
        )
    else:
        conn.execute(
            "UPDATE sources SET consecutive_failures = ? WHERE id = ?",
            (new_failures, source_id),
        )
    conn.commit()
    logger.warning("Source %s failed (%s): %s [%d/%d]",
                   source["source_key"], "hard" if hard else "soft",
                   result.error, new_failures, threshold)


async def run_sync(conn: sqlite3.Connection, source_key: str | None = None) -> dict:
    """Run sync for all eligible sources (or a single source_key).

    Returns stats dict: {sources_ok, sources_failed, items_total}.
    """
    job_id = insert_job_run(conn, job_type="sync")
    sources = _get_syncable_sources(conn, source_key)

    sources_ok = 0
    sources_failed = 0
    items_total = 0
    last_type: str | None = None
    type_throttled: dict[str, float] = {}  # escalated delay after 429

    for src in sources:
        # Rate limit: delay between consecutive requests to the same source type
        src_type = src["type"]
        base_delay = SOURCE_TYPE_DELAY.get(src_type, 0)
        delay = type_throttled.get(src_type, base_delay)
        if delay and last_type == src_type:
            await asyncio.sleep(delay)
        last_type = src_type

        try:
            adapter = get_adapter(src_type)
            # Build config from DB fields + parsed config_yaml
            config = {"handle": src["handle"], "source_key": src["source_key"]}
            if src["config_yaml"]:
                import yaml
                extra = yaml.safe_load(src["config_yaml"]) or {}
                config.update(extra)
            result = await adapter.sync(config)
        except Exception as exc:
            result = SyncResult(
                source_key=src["source_key"], items=[], success=False, error=str(exc)
            )

        if result.success:
            stored = _handle_success(conn, src, result)
            items_total += stored
            sources_ok += 1
        else:
            _handle_failure(conn, src, result)
            sources_failed += 1
            # Escalate delay on 429 to avoid burning remaining sources
            if result.error and "429" in result.error:
                current = type_throttled.get(src_type, base_delay)
                type_throttled[src_type] = min(current * 2, 30.0)

    status = "ok" if sources_failed == 0 else ("partial" if sources_ok > 0 else "failed")
    stats = {"sources_ok": sources_ok, "sources_failed": sources_failed, "items_total": items_total}
    finish_job_run(conn, job_id, status=status, stats_json=json.dumps(stats))

    return stats
