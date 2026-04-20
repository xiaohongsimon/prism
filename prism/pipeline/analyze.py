"""Signal analysis pipeline: incremental (per-cluster) and daily batch."""

import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Optional

from prism.db import insert_job_run, finish_job_run
from prism.pipeline.llm import (
    call_llm, call_llm_json, call_claude_json, PROMPT_VERSION,
    INCREMENTAL_SYSTEM, INCREMENTAL_USER_TEMPLATE,
    VIDEO_SYSTEM, VIDEO_USER_TEMPLATE,
    DAILY_BATCH_SYSTEM, DAILY_BATCH_USER_TEMPLATE,
    NARRATIVE_SYSTEM, NARRATIVE_USER_TEMPLATE,
)

logger = logging.getLogger(__name__)


def _to_str(val) -> str:
    """Coerce LLM output to string — handles list/dict returns gracefully."""
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        return "\n".join(str(v) for v in val)
    return str(val) if val else ""


def _get_unanalyzed_clusters(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Find clusters that have no current signal yet."""
    return conn.execute(
        "SELECT c.* FROM clusters c "
        "LEFT JOIN signals s ON c.id = s.cluster_id AND s.is_current = 1 "
        "WHERE s.id IS NULL"
    ).fetchall()


def _get_clusters_for_date(conn: sqlite3.Connection, dt: str) -> list[sqlite3.Row]:
    """Get all clusters for a given date."""
    return conn.execute(
        "SELECT * FROM clusters WHERE date = ?", (dt,)
    ).fetchall()


def _get_yesterday_summary(conn: sqlite3.Connection, dt: str) -> str:
    """Build yesterday's summary from top 5 daily signals."""
    from datetime import timedelta
    d = datetime.strptime(dt, "%Y-%m-%d").date()
    yesterday = (d - timedelta(days=1)).isoformat()

    rows = conn.execute(
        "SELECT s.summary, c.topic_label FROM signals s "
        "JOIN clusters c ON s.cluster_id = c.id "
        "WHERE c.date = ? AND s.analysis_type = 'daily' AND s.is_current = 1 "
        "ORDER BY s.signal_strength DESC LIMIT 5",
        (yesterday,),
    ).fetchall()

    if not rows:
        return "无昨日数据（系统首日运行）"

    lines = [f"- {r['topic_label']}: {r['summary']}" for r in rows]
    return "\n".join(lines)


def _split_batches(clusters: list[sqlite3.Row], max_tokens: int = 60000) -> tuple[list, list]:
    """Split clusters into main batch (within budget) and supplementary batch.

    Sort by cross_source potential (item_count desc). Estimate tokens as len(merged_context)/4.
    """
    sorted_clusters = sorted(clusters, key=lambda c: c["item_count"], reverse=True)
    main = []
    supplementary = []
    budget = 0

    for c in sorted_clusters:
        est_tokens = len(c["merged_context"]) // 4
        if budget + est_tokens <= max_tokens:
            main.append(c)
            budget += est_tokens
        else:
            supplementary.append(c)

    return main, supplementary


def _analyze_one_cluster(cluster_data: dict, model: Optional[str] = None,
                         job_id: Optional[int] = None,
                         job_type: Optional[str] = None) -> Optional[dict]:
    """Analyze a single cluster via LLM (thread-safe, no DB access)."""
    is_video = cluster_data.get("is_video", False)
    if is_video and len(cluster_data.get("merged_context", "")) > 500:
        system = VIDEO_SYSTEM
        user_template = VIDEO_USER_TEMPLATE
    else:
        system = INCREMENTAL_SYSTEM
        user_template = INCREMENTAL_USER_TEMPLATE

    prompt = user_template.format(
        topic_label=cluster_data["topic_label"],
        item_count=cluster_data.get("item_count", 1),
        merged_context=cluster_data["merged_context"],
    )
    try:
        result = call_llm_json(
            prompt, system=system, model=model,
            max_tokens=6000,
            session_id=f"job-{job_id}" if job_id else None,
            project="簇级摘要",
        )
        # LLM sometimes returns a list instead of dict — take first element
        if isinstance(result, list):
            result = result[0] if result else {}
        if not isinstance(result, dict):
            logger.error("LLM returned non-dict for cluster %d: %s", cluster_data["id"], type(result))
            return None
        # For video analysis, merge key_insights into summary and content_zh
        if is_video and "key_insights" in result:
            insights = result.get("key_insights", [])
            if insights:
                insights_text = "\n".join(f"• {ins}" for ins in insights)
                result["summary"] = result.get("summary", "") + "\n\n💡 核心洞察：\n" + insights_text
                # Ensure content_zh is populated for video cards
                if not result.get("content_zh"):
                    result["content_zh"] = result.get("summary", "")
        return result
    except Exception as exc:
        logger.error("Incremental analysis failed for cluster %d: %s", cluster_data["id"], exc)
        return None


def run_incremental_analysis(conn: sqlite3.Connection, model: Optional[str] = None,
                             max_workers: int = 8) -> int:
    """Analyze clusters without signals. Returns count of signals created."""
    clusters = _get_unanalyzed_clusters(conn)
    if not clusters:
        return 0

    job_id = insert_job_run(conn, job_type="analyze_incremental")
    count = 0

    # Detect which clusters are from YouTube (for video-specific analysis)
    youtube_cluster_ids = set()
    yt_rows = conn.execute(
        """
        SELECT DISTINCT ci.cluster_id FROM cluster_items ci
        JOIN raw_items ri ON ri.id = ci.raw_item_id
        JOIN sources s ON s.id = ri.source_id
        WHERE s.type = 'youtube'
        """
    ).fetchall()
    for r in yt_rows:
        youtube_cluster_ids.add(r["cluster_id"])

    # Prepare cluster data dicts for thread-safe access
    cluster_dicts = [
        {"id": c["id"], "topic_label": c["topic_label"],
         "item_count": c["item_count"], "merged_context": c["merged_context"],
         "is_video": c["id"] in youtube_cluster_ids}
        for c in clusters
    ]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_cluster = {
            executor.submit(
                _analyze_one_cluster, cd, model,
                job_id, "analyze_incremental",
            ): cd
            for cd in cluster_dicts
        }
        for future in as_completed(future_to_cluster):
            cd = future_to_cluster[future]
            result = future.result()
            if result is None:
                continue

            conn.execute(
                "INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, "
                "why_it_matters, action, tl_perspective, tags_json, content_zh, analysis_type, "
                "model_id, prompt_version, job_run_id, is_current) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'incremental', ?, ?, ?, 1)",
                (
                    cd["id"],
                    _to_str(result.get("summary", "")),
                    _to_str(result.get("signal_layer", "noise")),
                    result.get("signal_strength", 1),
                    _to_str(result.get("why_it_matters", "")),
                    _to_str(result.get("action", "")),
                    _to_str(result.get("tl_perspective", "")),
                    json.dumps(result.get("tags", []), ensure_ascii=False),
                    _to_str(result.get("content_zh", "")),
                    model or "",
                    PROMPT_VERSION,
                    job_id,
                ),
            )
            conn.commit()
            count += 1

    finish_job_run(conn, job_id, status="ok" if count > 0 else "failed",
                   stats_json=json.dumps({"signals_created": count}))
    return count


def run_daily_analysis(conn: sqlite3.Connection, dt: Optional[str] = None,
                       model: Optional[str] = None, date: Optional[str] = None) -> dict:
    """Run daily batch analysis for all clusters on a date.

    Returns stats dict.
    """
    # Support both 'dt' and 'date' parameter names for compatibility
    analysis_date = dt or date or datetime.now().strftime("%Y-%m-%d")

    clusters = _get_clusters_for_date(conn, analysis_date)
    if not clusters:
        logger.warning("No clusters for %s, will generate narrative from recent signals", analysis_date)

    job_id = insert_job_run(conn, job_type="analyze_daily")

    # ── Step 1: Generate narrative from existing incremental signals ──
    # (Signals already created by hourly incremental analysis — no need to redo)
    from prism.config import settings as _cfg
    daily_model = model or _cfg.llm_premium_model or None

    # Get top signals from last 2 days (not just one date, to handle timezone gaps)
    from datetime import timedelta
    cutoff = (datetime.strptime(analysis_date, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
    top_signals = conn.execute(
        """SELECT s.summary, s.why_it_matters, s.signal_layer, s.signal_strength,
                  c.id AS cluster_id, c.topic_label, c.date
           FROM signals s JOIN clusters c ON s.cluster_id = c.id
           WHERE c.date >= ? AND s.is_current = 1
           ORDER BY c.date DESC, s.signal_strength DESC, s.created_at DESC
           LIMIT 20""",
        (cutoff,),
    ).fetchall()

    narrative = ""
    if top_signals:
        signals_text = ""
        for s in top_signals:
            signals_text += (
                f"\n- (Cluster {s['cluster_id']}) {s['topic_label']}"
                f" [{s['signal_layer']}, strength={s['signal_strength']}]"
                f"\n  摘要: {s['summary'][:200]}"
            )
            if s["why_it_matters"]:
                signals_text += f"\n  重要性: {s['why_it_matters'][:100]}"

        narrative_prompt = NARRATIVE_USER_TEMPLATE.format(
            date=analysis_date,
            signal_count=len(top_signals),
            signals_text=signals_text,
        )
        try:
            narrative = call_llm(narrative_prompt, system=NARRATIVE_SYSTEM,
                                model=daily_model, max_tokens=2048,
                                session_id=f"job-{job_id}",
                                project="日级综述")
            # Strip thinking tags if present
            import re
            narrative = re.sub(r"<think>.*?</think>", "", narrative, flags=re.DOTALL).strip()
            # QA: check for repetition
            if re.search(r'(.{2,})\1{2,}', narrative):
                logger.warning("Narrative has repetition, retrying...")
                retry = call_llm(narrative_prompt, system=NARRATIVE_SYSTEM,
                                model=daily_model, max_tokens=2048,
                                session_id=f"job-{job_id}",
                                project="日级综述")
                retry = re.sub(r"<think>.*?</think>", "", retry, flags=re.DOTALL).strip()
                if not re.search(r'(.{2,})\1{2,}', retry):
                    narrative = retry
        except Exception as exc:
            logger.error("Narrative generation failed: %s", exc)
            narrative = ""

    stats = {
        "signals_created": len(top_signals),
        "cross_links": 0,
        "briefing_narrative": narrative,
    }
    finish_job_run(conn, job_id, status="ok", stats_json=json.dumps(stats, ensure_ascii=False))

    return stats
