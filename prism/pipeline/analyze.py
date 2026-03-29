"""Signal analysis pipeline: incremental (per-cluster) and daily batch."""

import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Optional

from prism.db import insert_job_run, finish_job_run
from prism.pipeline.llm import (
    call_llm_json, PROMPT_VERSION,
    INCREMENTAL_SYSTEM, INCREMENTAL_USER_TEMPLATE,
    VIDEO_SYSTEM, VIDEO_USER_TEMPLATE,
    DAILY_BATCH_SYSTEM, DAILY_BATCH_USER_TEMPLATE,
)

logger = logging.getLogger(__name__)


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


def _analyze_one_cluster(cluster_data: dict, model: Optional[str] = None) -> Optional[dict]:
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
        result = call_llm_json(prompt, system=system, model=model)
        # For video analysis, merge key_insights into summary for display
        if is_video and "key_insights" in result:
            insights = result.get("key_insights", [])
            if insights:
                result["summary"] = result.get("summary", "") + "\n\n💡 核心洞察：\n" + "\n".join(
                    f"• {ins}" for ins in insights
                )
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
            executor.submit(_analyze_one_cluster, cd, model): cd
            for cd in cluster_dicts
        }
        for future in as_completed(future_to_cluster):
            cd = future_to_cluster[future]
            result = future.result()
            if result is None:
                continue

            conn.execute(
                "INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, "
                "why_it_matters, action, tl_perspective, tags_json, analysis_type, "
                "model_id, prompt_version, job_run_id, is_current) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'incremental', ?, ?, ?, 1)",
                (
                    cd["id"],
                    result.get("summary", ""),
                    result.get("signal_layer", "noise"),
                    result.get("signal_strength", 1),
                    result.get("why_it_matters", ""),
                    result.get("action", ""),
                    result.get("tl_perspective", ""),
                    json.dumps(result.get("tags", []), ensure_ascii=False),
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
        return {"signals_created": 0}

    job_id = insert_job_run(conn, job_type="analyze_daily")
    yesterday_summary = _get_yesterday_summary(conn, analysis_date)

    # Build batch prompt
    main_batch, supplementary = _split_batches(clusters)

    clusters_text = ""
    for c in main_batch:
        clusters_text += f"\n### 聚类 {c['id']}: {c['topic_label']} ({c['item_count']} 条)\n"
        clusters_text += c["merged_context"][:2000] + "\n"

    prompt = DAILY_BATCH_USER_TEMPLATE.format(
        date=analysis_date,
        yesterday_summary=yesterday_summary,
        cluster_count=len(main_batch),
        clusters_text=clusters_text,
    )

    try:
        result = call_llm_json(prompt, system=DAILY_BATCH_SYSTEM, model=model)
    except Exception as exc:
        logger.error("Daily analysis failed: %s", exc)
        finish_job_run(conn, job_id, status="failed", stats_json=json.dumps({"error": str(exc)}))
        return {"signals_created": 0, "error": str(exc)}

    # Invalidate incremental signals for this date
    conn.execute(
        "UPDATE signals SET is_current = 0 "
        "WHERE cluster_id IN (SELECT id FROM clusters WHERE date = ?) "
        "AND analysis_type = 'incremental' AND is_current = 1",
        (analysis_date,),
    )

    # Insert daily signals
    signals_created = 0
    for cs in result.get("clusters", []):
        conn.execute(
            "INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, "
            "why_it_matters, action, tl_perspective, tags_json, analysis_type, "
            "model_id, prompt_version, job_run_id, is_current) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'daily', ?, ?, ?, 1)",
            (
                cs["cluster_id"],
                cs.get("summary", ""),
                cs.get("signal_layer", "noise"),
                cs.get("signal_strength", 1),
                cs.get("why_it_matters", ""),
                cs.get("action", ""),
                cs.get("tl_perspective", ""),
                json.dumps(cs.get("tags", []), ensure_ascii=False),
                model or "",
                PROMPT_VERSION,
                job_id,
            ),
        )
        signals_created += 1

    # Insert cross_links
    for cl in result.get("cross_links", []):
        conn.execute(
            "INSERT INTO cross_links (cluster_a_id, cluster_b_id, relation_type, reason, job_run_id, is_current) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (cl["cluster_a_id"], cl["cluster_b_id"], cl["relation_type"], cl.get("reason", ""), job_id),
        )

    conn.commit()

    # Store briefing_narrative in job_run stats
    stats = {
        "signals_created": signals_created,
        "cross_links": len(result.get("cross_links", [])),
        "briefing_narrative": result.get("briefing_narrative", ""),
    }
    finish_job_run(conn, job_id, status="ok", stats_json=json.dumps(stats, ensure_ascii=False))

    # Handle supplementary batch (single-cluster analysis, no cross_links)
    for c in supplementary:
        sup_prompt = INCREMENTAL_USER_TEMPLATE.format(
            topic_label=c["topic_label"],
            item_count=c["item_count"],
            merged_context=c["merged_context"],
        )
        try:
            sup_result = call_llm_json(sup_prompt, system=INCREMENTAL_SYSTEM, model=model)
            conn.execute(
                "INSERT INTO signals (cluster_id, summary, signal_layer, signal_strength, "
                "why_it_matters, action, tl_perspective, tags_json, analysis_type, "
                "model_id, prompt_version, job_run_id, is_current) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'daily', ?, ?, ?, 1)",
                (
                    c["id"],
                    sup_result.get("summary", ""),
                    sup_result.get("signal_layer", "noise"),
                    sup_result.get("signal_strength", 1),
                    sup_result.get("why_it_matters", ""),
                    sup_result.get("action", ""),
                    sup_result.get("tl_perspective", ""),
                    json.dumps(sup_result.get("tags", []), ensure_ascii=False),
                    model or "",
                    PROMPT_VERSION,
                    job_id,
                ),
            )
            conn.commit()
            signals_created += 1
        except Exception as exc:
            logger.error("Supplementary analysis failed for cluster %d: %s", c["id"], exc)

    return stats
