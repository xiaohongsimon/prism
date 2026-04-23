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
    INCREMENTAL_TRIAGE_SYSTEM, INCREMENTAL_TRIAGE_USER_TEMPLATE,
    INCREMENTAL_EXPAND_SYSTEM, INCREMENTAL_EXPAND_USER_TEMPLATE,
    VIDEO_SYSTEM, VIDEO_USER_TEMPLATE,
    DAILY_BATCH_SYSTEM, DAILY_BATCH_USER_TEMPLATE,
    NARRATIVE_SYSTEM, NARRATIVE_USER_TEMPLATE,
)
from prism.pipeline.llm_tasks import Scope, Task

# Default models for the two-stage pipeline. Can be overridden via CLI.
# Stage 1: cheap model across all clusters. Stage 2: reasoning model on
# high-strength survivors only.
TRIAGE_MODEL_DEFAULT = "gemma-4-26b-a4b-it-8bit"
EXPAND_MODEL_DEFAULT = "Qwen3.6-35B-A3B-8bit"

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
            max_tokens=16000,
            session_id=f"job-{job_id}" if job_id else None,
            task=Task.SUMMARIZE, scope=Scope.CLUSTER,
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


def _triage_one_cluster(cluster_data: dict, model: str, job_id: Optional[int]) -> Optional[dict]:
    """Stage 1 worker: fast 5-field signal classification. No DB access.

    Uses the cheap model across every cluster. Video clusters still use
    VIDEO_SYSTEM since their transcripts need different handling — those
    don't benefit from the expand split either (they already produce
    content_zh via key_insights fusion).
    """
    is_video = cluster_data.get("is_video", False)
    if is_video and len(cluster_data.get("merged_context", "")) > 500:
        # Video path: same as before, produces rich output in one shot
        system = VIDEO_SYSTEM
        user_template = VIDEO_USER_TEMPLATE
        # Video uses the reasoning model because transcripts need deep comprehension
        call_model = EXPAND_MODEL_DEFAULT
        max_tokens = 16000
    else:
        system = INCREMENTAL_TRIAGE_SYSTEM
        user_template = INCREMENTAL_TRIAGE_USER_TEMPLATE
        call_model = model
        max_tokens = 2048

    prompt = user_template.format(
        topic_label=cluster_data["topic_label"],
        item_count=cluster_data.get("item_count", 1),
        merged_context=cluster_data["merged_context"],
    )
    try:
        result = call_llm_json(
            prompt, system=system, model=call_model,
            max_tokens=max_tokens,
            session_id=f"job-{job_id}" if job_id else None,
            task=Task.SUMMARIZE if is_video else Task.CLASSIFY,
            scope=Scope.CLUSTER,
        )
        if isinstance(result, list):
            result = result[0] if result else {}
        if not isinstance(result, dict):
            logger.error("Triage returned non-dict for cluster %d: %s",
                         cluster_data["id"], type(result))
            return None
        # Video path merges key_insights into summary/content_zh like before
        if is_video and "key_insights" in result:
            insights = result.get("key_insights", [])
            if insights:
                insights_text = "\n".join(f"• {ins}" for ins in insights)
                result["summary"] = result.get("summary", "") + "\n\n💡 核心洞察：\n" + insights_text
                if not result.get("content_zh"):
                    result["content_zh"] = result.get("summary", "")
        return result
    except Exception as exc:
        logger.error("Triage failed for cluster %d: %s", cluster_data["id"], exc)
        return None


def _expand_one_signal(signal_data: dict, model: str, job_id: Optional[int]) -> Optional[dict]:
    """Stage 2 worker: deep translation + TL perspective. No DB access.

    Input carries the triage summary so the expand model has context about
    why this signal earned deep treatment.
    """
    prompt = INCREMENTAL_EXPAND_USER_TEMPLATE.format(
        topic_label=signal_data["topic_label"],
        summary=signal_data["summary"],
        why_it_matters=signal_data["why_it_matters"],
        signal_strength=signal_data["signal_strength"],
        merged_context=signal_data["merged_context"],
    )
    try:
        result = call_llm_json(
            prompt, system=INCREMENTAL_EXPAND_SYSTEM, model=model,
            max_tokens=16000,
            session_id=f"job-{job_id}" if job_id else None,
            task=Task.SUMMARIZE, scope=Scope.CLUSTER,
        )
        if isinstance(result, list):
            result = result[0] if result else {}
        if not isinstance(result, dict):
            logger.error("Expand returned non-dict for signal %d: %s",
                         signal_data["id"], type(result))
            return None
        return result
    except Exception as exc:
        logger.error("Expand failed for signal %d: %s", signal_data["id"], exc)
        return None


def run_triage(conn: sqlite3.Connection, model: Optional[str] = None,
               max_workers: int = 8) -> int:
    """Stage 1: triage all unanalyzed clusters with the cheap model.

    Produces signal rows with summary/layer/strength/why/tags populated but
    content_zh and tl_perspective left empty — those are the expand stage's
    job, run only for high-strength survivors.

    Returns count of signals created.
    """
    clusters = _get_unanalyzed_clusters(conn)
    if not clusters:
        return 0

    triage_model = model or TRIAGE_MODEL_DEFAULT
    job_id = insert_job_run(conn, job_type="analyze_triage")
    count = 0

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

    cluster_dicts = [
        {"id": c["id"], "topic_label": c["topic_label"],
         "item_count": c["item_count"], "merged_context": c["merged_context"],
         "is_video": c["id"] in youtube_cluster_ids}
        for c in clusters
    ]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_cluster = {
            executor.submit(_triage_one_cluster, cd, triage_model, job_id): cd
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
                    triage_model,
                    PROMPT_VERSION,
                    job_id,
                ),
            )
            conn.commit()
            count += 1

    finish_job_run(conn, job_id, status="ok" if count > 0 else "failed",
                   stats_json=json.dumps({"signals_created": count}))
    return count


def run_expand(conn: sqlite3.Connection, model: Optional[str] = None,
               min_strength: int = 4, limit: int = 30,
               max_workers: int = 4) -> int:
    """Stage 2: deep-read top-N highest-strength signals that still lack
    content_zh. Populates content_zh + tl_perspective + action in place.

    Why the strict filter: only ~20% of triaged signals are worth the
    reasoning-model pass. Bounding by `limit` keeps hourly runtime predictable
    even when sync ingests a huge backlog.

    Returns count of signals expanded.
    """
    expand_model = model or EXPAND_MODEL_DEFAULT

    # Find signals that need expansion: high strength, currently-active,
    # no content_zh yet, not from video path (video already fills content_zh).
    rows = conn.execute(
        """
        SELECT s.id AS signal_id, s.cluster_id, s.summary, s.why_it_matters,
               s.signal_strength, c.topic_label, c.merged_context
        FROM signals s
        JOIN clusters c ON s.cluster_id = c.id
        WHERE s.is_current = 1
          AND s.analysis_type = 'incremental'
          AND s.signal_strength >= ?
          AND COALESCE(s.content_zh, '') = ''
          AND s.signal_layer != 'noise'
        ORDER BY s.signal_strength DESC, s.created_at DESC
        LIMIT ?
        """,
        (min_strength, limit),
    ).fetchall()

    if not rows:
        return 0

    job_id = insert_job_run(conn, job_type="analyze_expand")
    count = 0

    signal_dicts = [
        {"id": r["signal_id"], "cluster_id": r["cluster_id"],
         "summary": r["summary"], "why_it_matters": r["why_it_matters"],
         "signal_strength": r["signal_strength"],
         "topic_label": r["topic_label"],
         "merged_context": r["merged_context"]}
        for r in rows
    ]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_signal = {
            executor.submit(_expand_one_signal, sd, expand_model, job_id): sd
            for sd in signal_dicts
        }
        for future in as_completed(future_to_signal):
            sd = future_to_signal[future]
            result = future.result()
            if result is None:
                continue

            conn.execute(
                "UPDATE signals SET content_zh = ?, tl_perspective = ?, action = ?, "
                "model_id = ? WHERE id = ?",
                (
                    _to_str(result.get("content_zh", "")),
                    _to_str(result.get("tl_perspective", "")),
                    _to_str(result.get("action", "")),
                    expand_model,
                    sd["id"],
                ),
            )
            conn.commit()
            count += 1

    finish_job_run(conn, job_id, status="ok" if count > 0 else "failed",
                   stats_json=json.dumps({"signals_expanded": count}))
    return count


def run_incremental_analysis(conn: sqlite3.Connection, model: Optional[str] = None,
                             max_workers: int = 8,
                             *,
                             expand_model: Optional[str] = None,
                             min_strength: int = 4,
                             expand_limit: int = 30) -> int:
    """Backward-compat entry point: run triage then expand sequentially.

    `model` now controls the triage (stage 1) model for backward compat;
    `expand_model` controls stage 2. Both default to their stage's default.

    Returns count of signals triaged (stage 1 output; stage 2 updates in place).
    """
    triaged = run_triage(conn, model=model, max_workers=max_workers)
    if triaged > 0:
        run_expand(conn, model=expand_model, min_strength=min_strength,
                   limit=expand_limit, max_workers=min(max_workers, 4))
    return triaged


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
                                task=Task.SUMMARIZE, scope=Scope.DAILY)
            # Strip thinking tags if present
            import re
            narrative = re.sub(r"<think>.*?</think>", "", narrative, flags=re.DOTALL).strip()
            # QA: check for repetition
            if re.search(r'(.{2,})\1{2,}', narrative):
                logger.warning("Narrative has repetition, retrying...")
                retry = call_llm(narrative_prompt, system=NARRATIVE_SYSTEM,
                                model=daily_model, max_tokens=2048,
                                session_id=f"job-{job_id}",
                                task=Task.SUMMARIZE, scope=Scope.DAILY)
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
