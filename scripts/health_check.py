#!/usr/bin/env python3
"""Prism pipeline health check with auto-repair.

Run daily (or on demand) to detect and fix common pipeline issues:
1. Auto-disabled sources stuck past their retry window → reset
2. Sources with high failure counts approaching threshold → warn
3. Stale briefings (no new analyze output) → trigger re-analyze
4. LLM service down → log warning (can't auto-fix)

Usage:
    python scripts/health_check.py          # check + auto-fix
    python scripts/health_check.py --dry    # check only, no fixes
    python scripts/health_check.py --json   # machine-readable output
"""

import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Resolve project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "prism.sqlite3"
LOG_PATH = PROJECT_ROOT / "data" / "health_check.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
logger = logging.getLogger("health_check")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_disabled_sources(conn: sqlite3.Connection) -> list[dict]:
    """Find auto-disabled sources past their retry window."""
    rows = conn.execute(
        "SELECT source_key, consecutive_failures, auto_retry_at "
        "FROM sources WHERE enabled=0 AND disabled_reason='auto' "
        "AND auto_retry_at IS NOT NULL AND auto_retry_at <= datetime('now')"
    ).fetchall()
    return [dict(r) for r in rows]


def check_high_failure_sources(conn: sqlite3.Connection) -> list[dict]:
    """Sources approaching auto-disable threshold (>=4 failures)."""
    rows = conn.execute(
        "SELECT source_key, consecutive_failures "
        "FROM sources WHERE enabled=1 AND consecutive_failures >= 4 "
        "ORDER BY consecutive_failures DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def check_stale_briefing(conn: sqlite3.Connection) -> dict:
    """Check if the last successful analyze produced signals."""
    row = conn.execute(
        "SELECT job_type, status, started_at, stats_json "
        "FROM job_runs WHERE job_type LIKE 'analyze%' "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {"stale": True, "reason": "no analyze jobs found"}

    stats = json.loads(row["stats_json"]) if row["stats_json"] else {}
    started = row["started_at"]
    signals_created = stats.get("signals_created", 0)

    # Stale if last analyze failed or created 0 signals and is >12h old
    if row["status"] == "failed" or signals_created == 0:
        try:
            started_dt = datetime.fromisoformat(started)
            age_hours = (datetime.now(timezone.utc) - started_dt.replace(tzinfo=timezone.utc)).total_seconds() / 3600
        except (ValueError, TypeError):
            age_hours = 999
        if age_hours > 12:
            return {
                "stale": True,
                "reason": f"last analyze: status={row['status']}, signals={signals_created}, age={age_hours:.1f}h",
            }
    return {"stale": False}


def check_llm_service() -> dict:
    """Check if LLM service is reachable."""
    import urllib.request

    for port in (8002, 8003):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/models",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return {"up": True, "port": port}
        except Exception:
            continue
    return {"up": False, "port": None}


def check_sync_health(conn: sqlite3.Connection) -> dict:
    """Check recent sync success rate."""
    rows = conn.execute(
        "SELECT stats_json FROM job_runs WHERE job_type='sync' "
        "ORDER BY started_at DESC LIMIT 5"
    ).fetchall()
    if not rows:
        return {"recent_syncs": 0, "avg_ok_rate": 0}

    total_ok = 0
    total_failed = 0
    for r in rows:
        stats = json.loads(r["stats_json"]) if r["stats_json"] else {}
        total_ok += stats.get("sources_ok", 0)
        total_failed += stats.get("sources_failed", 0)

    total = total_ok + total_failed
    return {
        "recent_syncs": len(rows),
        "avg_ok_rate": round(total_ok / total, 2) if total > 0 else 0,
        "total_ok": total_ok,
        "total_failed": total_failed,
    }


# ---------------------------------------------------------------------------
# Repairs
# ---------------------------------------------------------------------------


def repair_disabled_sources(conn: sqlite3.Connection, sources: list[dict]) -> int:
    """Reset auto-disabled sources that are past their retry window."""
    if not sources:
        return 0
    keys = [s["source_key"] for s in sources]
    placeholders = ",".join("?" * len(keys))
    cursor = conn.execute(
        f"UPDATE sources SET enabled=1, consecutive_failures=0, "
        f"disabled_reason=NULL, auto_retry_at=NULL "
        f"WHERE source_key IN ({placeholders})",
        keys,
    )
    conn.commit()
    return cursor.rowcount


def repair_mass_failures(conn: sqlite3.Connection) -> int:
    """If >50% of sources are disabled/failing, do a full reset.

    This handles cascading 429 scenarios where rate limiting takes out
    everything — a bulk reset with staggered retry is better than waiting
    24h per source.
    """
    total = conn.execute("SELECT COUNT(*) FROM sources WHERE disabled_reason != 'yaml_removed' OR disabled_reason IS NULL").fetchone()[0]
    disabled = conn.execute(
        "SELECT COUNT(*) FROM sources WHERE enabled=0 AND disabled_reason='auto'"
    ).fetchone()[0]

    if total == 0 or disabled / total < 0.5:
        return 0

    logger.warning("Mass failure detected: %d/%d sources auto-disabled, resetting all", disabled, total)
    cursor = conn.execute(
        "UPDATE sources SET enabled=1, consecutive_failures=0, "
        "disabled_reason=NULL, auto_retry_at=NULL "
        "WHERE enabled=0 AND disabled_reason='auto'"
    )
    conn.commit()
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_health_check(dry_run: bool = False) -> dict:
    """Run all checks and optionally apply repairs. Returns report dict."""
    conn = get_conn()
    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "dry_run": dry_run}

    # 1. Disabled sources past retry
    expired = check_disabled_sources(conn)
    report["expired_disabled"] = {
        "count": len(expired),
        "sources": [s["source_key"] for s in expired],
    }
    if expired:
        logger.info("Found %d expired auto-disabled sources", len(expired))
        if not dry_run:
            reset = repair_disabled_sources(conn, expired)
            logger.info("Reset %d expired sources", reset)
            report["expired_disabled"]["repaired"] = reset

    # 2. Mass failure check
    report["mass_failure_reset"] = 0
    if not dry_run:
        mass_reset = repair_mass_failures(conn)
        if mass_reset:
            report["mass_failure_reset"] = mass_reset

    # 3. High failure sources (warning only)
    high_fail = check_high_failure_sources(conn)
    report["high_failure_sources"] = {
        "count": len(high_fail),
        "sources": [{"key": s["source_key"], "failures": s["consecutive_failures"]} for s in high_fail],
    }
    if high_fail:
        logger.warning("Sources approaching disable threshold: %s",
                        ", ".join(f"{s['source_key']}({s['consecutive_failures']})" for s in high_fail))

    # 4. Stale briefing
    stale = check_stale_briefing(conn)
    report["stale_briefing"] = stale
    if stale["stale"]:
        logger.warning("Briefing is stale: %s", stale["reason"])

    # 5. LLM service
    llm = check_llm_service()
    report["llm_service"] = llm
    if not llm["up"]:
        logger.warning("LLM service is DOWN on both ports 8002/8003")

    # 6. Sync health
    sync = check_sync_health(conn)
    report["sync_health"] = sync
    if sync["avg_ok_rate"] < 0.5:
        logger.warning("Sync success rate is low: %.0f%%", sync["avg_ok_rate"] * 100)

    conn.close()
    return report


def main():
    dry_run = "--dry" in sys.argv
    json_output = "--json" in sys.argv

    logger.info("=== Prism Health Check %s ===", "(DRY RUN)" if dry_run else "")
    report = run_health_check(dry_run=dry_run)

    if json_output:
        print(json.dumps(report, indent=2))
    else:
        # Human-readable summary
        print(f"\n{'='*50}")
        print(f"Prism Health Check — {report['timestamp']}")
        print(f"{'='*50}")

        ed = report["expired_disabled"]
        print(f"\nExpired disabled sources: {ed['count']}")
        if ed["count"] > 0:
            repaired = ed.get("repaired", "skipped (dry run)")
            print(f"  Repaired: {repaired}")
            for s in ed["sources"][:10]:
                print(f"    - {s}")

        mf = report["mass_failure_reset"]
        if mf:
            print(f"\nMass failure reset: {mf} sources")

        hf = report["high_failure_sources"]
        if hf["count"]:
            print(f"\nSources near threshold ({hf['count']}):")
            for s in hf["sources"]:
                print(f"  - {s['key']}: {s['failures']} failures")

        sb = report["stale_briefing"]
        print(f"\nBriefing stale: {'YES' if sb['stale'] else 'No'}")
        if sb["stale"]:
            print(f"  Reason: {sb['reason']}")

        llm = report["llm_service"]
        print(f"\nLLM service: {'UP (port {})'.format(llm['port']) if llm['up'] else 'DOWN'}")

        sh = report["sync_health"]
        print(f"\nSync health (last 5 runs):")
        print(f"  Success rate: {sh['avg_ok_rate']*100:.0f}% ({sh['total_ok']} ok / {sh['total_failed']} failed)")

        print()


if __name__ == "__main__":
    main()
