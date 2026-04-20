"""Persona snapshot management + LLM extraction.

A persona snapshot is the user's self-description of who they are right now
and what they want Prism to surface. Snapshots are versioned; only one is
active at a time.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from prism.pipeline.llm import call_llm_json


_PERSONA_BIAS_CLIP = 5.0

PERSONA_PROMPT_SYSTEM = (
    "你是 Prism 个人推荐系统的偏好提取助手。"
    "根据用户的 persona 描述，输出结构化 JSON："
    "1) summary: 一句话总结用户当前身份与关注点（中文）；"
    "2) bias_weights: 一个数组，每项 {dimension, key, weight}，"
    "   dimension ∈ [tag, author, source, layer]，weight 在 [-5, 5]；"
    "3) candidate_sources: 20-30 个候选信号源，每项包含 "
    "   {type, handle_or_url, display_name, rationale, category}，"
    "   type 可以是 x/rss/youtube/hn/arxiv 等，"
    "   若 type=x 提供 handle，否则提供 url。"
    "严格输出 JSON，不要任何额外文字。"
)


def _build_extract_prompt(
    answers: dict, free_text: str, seed_handles: list[str],
    current_top_prefs: list[tuple[str, str, float]],
) -> str:
    import json as _json
    top_prefs_text = (
        "\n".join(f"  - {d}/{k}: {w:+.2f}" for d, k, w in current_top_prefs[:20])
        or "  (尚无学习到的偏好)"
    )
    return (
        "【用户结构化答题】\n" + _json.dumps(answers, ensure_ascii=False, indent=2)
        + "\n\n【自由文字补充】\n" + (free_text or "(无)")
        + "\n\n【用户提供的种子账号】\n"
        + ("\n".join(f"  - {h}" for h in seed_handles) if seed_handles else "  (无)")
        + "\n\n【当前系统已学到的偏好 top/bottom】\n" + top_prefs_text
        + "\n\n请据此给出 summary / bias_weights / candidate_sources。"
    )


def _fetch_current_top_prefs(conn) -> list[tuple[str, str, float]]:
    top = conn.execute(
        "SELECT dimension, key, weight FROM preference_weights "
        "WHERE dimension != 'persona_bias' "
        "ORDER BY weight DESC LIMIT 15"
    ).fetchall()
    bot = conn.execute(
        "SELECT dimension, key, weight FROM preference_weights "
        "WHERE dimension != 'persona_bias' "
        "ORDER BY weight ASC LIMIT 15"
    ).fetchall()
    return [tuple(r) for r in top] + [tuple(r) for r in bot]


def extract_from_snapshot(conn, snapshot_id: int) -> tuple[int, int]:
    """Run LLM extraction on a persona snapshot.

    Writes bias weights to preference_weights (dimension='persona_bias'),
    zeroes out prior persona_bias rows not present in the new extraction,
    and creates source_proposals entries with origin='persona'.

    Returns (num_bias_weights_written, num_source_proposals_written).
    """
    import json as _json

    row = conn.execute(
        "SELECT answers_json, free_text, seed_handles_json "
        "FROM persona_snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"persona_snapshot id={snapshot_id} not found")
    answers = _json.loads(row[0])
    free_text = row[1] or ""
    seed_handles = _json.loads(row[2] or "[]")

    prompt = _build_extract_prompt(
        answers, free_text, seed_handles, _fetch_current_top_prefs(conn),
    )
    result = call_llm_json(prompt, system=PERSONA_PROMPT_SYSTEM, max_tokens=4096, project="画像提取")
    if not isinstance(result, dict):
        raise ValueError(
            f"LLM returned non-dict result for snapshot {snapshot_id}: "
            f"{type(result).__name__}"
        )

    summary = str(result.get("summary", "")).strip()
    bias_weights = result.get("bias_weights") or []
    candidates = result.get("candidate_sources") or []

    # All DB mutations inside a single transaction — rolls back on any error
    # so persona_bias is never left partially zeroed.
    n_weights = 0
    n_proposals = 0
    with conn:
        # Zero out all previous persona_bias rows (audit trail kept; rows not deleted)
        conn.execute(
            "UPDATE preference_weights SET weight = 0.0, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%S', 'now') "
            "WHERE dimension = 'persona_bias'"
        )

        for bw in bias_weights:
            dim = str(bw.get("dimension", "")).strip()
            key = str(bw.get("key", "")).strip()
            try:
                w = float(bw.get("weight", 0))
            except (TypeError, ValueError):
                continue
            if not dim or not key:
                continue
            w = max(-_PERSONA_BIAS_CLIP, min(_PERSONA_BIAS_CLIP, w))
            composite_key = f"{dim}/{key}"
            conn.execute(
                "INSERT INTO preference_weights (dimension, key, weight, updated_at) "
                "VALUES ('persona_bias', ?, ?, strftime('%Y-%m-%dT%H:%M:%S','now')) "
                "ON CONFLICT(dimension, key) DO UPDATE SET weight = excluded.weight, "
                "updated_at = excluded.updated_at",
                (composite_key, w),
            )
            n_weights += 1

        for cand in candidates:
            ctype = str(cand.get("type", "")).strip()
            if not ctype:
                continue
            cfg = {k: v for k, v in cand.items() if k not in ("rationale", "category", "display_name")}
            if "handle_or_url" in cfg:
                # normalise: if looks like URL, store as url; else as handle for x-like types
                v = cfg.pop("handle_or_url")
                if "://" in v:
                    cfg["url"] = v
                else:
                    cfg["handle"] = v
            display = str(cand.get("display_name") or cfg.get("handle") or cfg.get("url") or ctype)
            rationale = str(cand.get("rationale", ""))
            category = str(cand.get("category", ""))
            conn.execute(
                "INSERT INTO source_proposals "
                "(source_type, source_config_json, display_name, rationale, origin, origin_ref) "
                "VALUES (?, ?, ?, ?, 'persona', ?)",
                (ctype, _json.dumps(cfg, ensure_ascii=False),
                 display, f"{category}: {rationale}" if category else rationale,
                 str(snapshot_id)),
            )
            n_proposals += 1

        conn.execute(
            "UPDATE persona_snapshots SET extracted_summary = ? WHERE id = ?",
            (summary, snapshot_id),
        )
    return n_weights, n_proposals


def save_snapshot(
    conn: sqlite3.Connection,
    answers: dict[str, Any],
    free_text: str = "",
    seed_handles: list[str] | None = None,
) -> int:
    """Save a persona snapshot. Deactivates any prior active snapshot.
    Returns the new snapshot id."""
    seed_handles = seed_handles or []
    conn.execute("UPDATE persona_snapshots SET is_active = 0 WHERE is_active = 1")
    cur = conn.execute(
        "INSERT INTO persona_snapshots (answers_json, free_text, seed_handles_json, is_active) "
        "VALUES (?, ?, ?, 1)",
        (json.dumps(answers, ensure_ascii=False),
         free_text,
         json.dumps(seed_handles, ensure_ascii=False)),
    )
    conn.commit()
    return cur.lastrowid


def load_active_snapshot(conn: sqlite3.Connection) -> dict | None:
    """Return the currently active persona snapshot, or None if none exists."""
    row = conn.execute(
        "SELECT id, answers_json, free_text, seed_handles_json, extracted_summary, "
        "       is_active, created_at "
        "FROM persona_snapshots WHERE is_active = 1 "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "answers_json": row[1],
        "free_text": row[2],
        "seed_handles_json": row[3],
        "extracted_summary": row[4],
        "is_active": row[5],
        "created_at": row[6],
    }
