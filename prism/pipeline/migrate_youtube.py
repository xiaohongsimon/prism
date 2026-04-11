"""One-shot migration: split youtube:ai-interviews into per-channel sources.

Run: .venv/bin/python -m prism.pipeline.migrate_youtube [--execute]
Default is dry-run.
"""

import json
import sqlite3
import sys
import logging

logger = logging.getLogger(__name__)

CHANNEL_MAP = {
    "UCGWYKICLOE8Wxy7q3eYXmPA": "youtube:bestpartners",
    "UCkHrq03gWLLx6vjS2DOJ8aA": "youtube:sunriches",
    "UCUGLhcs3-3y_yhZZsgRzrzw": "youtube:storytellerfan",
    "UCjJklW6MyT2yjHEOrRu-FOA": "youtube:maskfinance",
    "UCQ1VQj-37kl2yS_VUhfQHsw": "youtube:a16z",
    "UC1Lk6WO-eKuYc6GHYbKVY2g": "youtube:sunlao",
    "UCVThAeUXPZcUfdYBvWGV3UA": "youtube:ltshijie",
    "UCn9_KbNANeyYREePe8YA2DA": "youtube:caijinglengyan",
}

OLD_SOURCE_KEY = "youtube:ai-interviews"


def validate_coverage(conn: sqlite3.Connection) -> tuple[int, int]:
    """Check how many raw_items have channel_id in raw_json. Returns (total, covered)."""
    old_source = conn.execute(
        "SELECT id FROM sources WHERE source_key = ?", (OLD_SOURCE_KEY,)
    ).fetchone()
    if not old_source:
        return 0, 0

    rows = conn.execute(
        "SELECT raw_json FROM raw_items WHERE source_id = ?", (old_source["id"],)
    ).fetchall()

    total = len(rows)
    covered = 0
    for r in rows:
        try:
            data = json.loads(r["raw_json"])
            if data.get("channel_id") in CHANNEL_MAP:
                covered += 1
        except (json.JSONDecodeError, TypeError):
            pass
    return total, covered


def migrate(conn: sqlite3.Connection, dry_run: bool = True) -> dict:
    """Migrate raw_items from old multi-channel source to new per-channel sources."""
    total, covered = validate_coverage(conn)
    pct = (covered * 100 // total) if total else 0
    print(f"Coverage check: {covered}/{total} ({pct}%) items have valid channel_id")

    if pct < 90:
        print("ABORT: coverage < 90%. Fix data first.")
        return {"aborted": True, "total": total, "covered": covered}

    old_source = conn.execute(
        "SELECT id FROM sources WHERE source_key = ?", (OLD_SOURCE_KEY,)
    ).fetchone()
    if not old_source:
        print("Old source not found, nothing to migrate.")
        return {"aborted": True, "reason": "old source not found"}
    old_source_id = old_source["id"]

    new_sources = {}
    for channel_id, new_key in CHANNEL_MAP.items():
        row = conn.execute(
            "SELECT id FROM sources WHERE source_key = ?", (new_key,)
        ).fetchone()
        if not row:
            print(f"WARNING: source {new_key} not found in DB. Run 'prism sync' first to reconcile.")
            if not dry_run:
                return {"aborted": True, "reason": f"missing source {new_key}"}
        else:
            new_sources[channel_id] = row["id"]

    migrated = 0
    skipped = 0
    items = conn.execute(
        "SELECT id, raw_json FROM raw_items WHERE source_id = ?", (old_source_id,)
    ).fetchall()

    for item in items:
        try:
            data = json.loads(item["raw_json"])
            channel_id = data.get("channel_id")
        except (json.JSONDecodeError, TypeError):
            channel_id = None

        if channel_id not in new_sources:
            skipped += 1
            continue

        new_source_id = new_sources[channel_id]
        if dry_run:
            print(f"  [DRY RUN] item {item['id']} -> source_id {new_source_id}")
        else:
            conn.execute(
                "UPDATE raw_items SET source_id = ? WHERE id = ?",
                (new_source_id, item["id"]),
            )
        migrated += 1

    if not dry_run:
        conn.execute(
            "UPDATE sources SET origin = 'yaml_removed', enabled = 0 WHERE id = ?",
            (old_source_id,),
        )
        conn.commit()

    stats = {"total": total, "migrated": migrated, "skipped": skipped, "dry_run": dry_run}
    print(f"Migration {'(DRY RUN) ' if dry_run else ''}complete: {stats}")
    return stats


if __name__ == "__main__":
    from prism.config import settings

    dry_run = "--execute" not in sys.argv

    conn = sqlite3.connect(str(settings.db_path))
    conn.row_factory = sqlite3.Row

    from prism.source_manager import reconcile_sources
    reconcile_sources(conn, settings.source_config)

    migrate(conn, dry_run=dry_run)
    conn.close()
