"""External feed consumer.

Reads unprocessed rows from external_feeds, extracts (via LLM) the author /
content-type / topics, writes extracted_json, optionally creates a source_proposal
if the hinted source is not already in sources.yaml.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from prism.pipeline.llm import call_llm_json


_SYSTEM_PROMPT = (
    "你是 Prism 的外部投喂分析器。用户贴来一个链接/话题，"
    "你要产出 JSON："
    "{url_canonical: str, author: str (若可知), content_type: 'tweet'|'article'|"
    "'video'|'paper'|'other', topics: [str], summary_zh: str (1-2句), "
    "source_hint: {type, handle|url, display_name}}"
    "严格 JSON，无额外文字。"
)


def _sources_yaml_path() -> Path:
    override = os.environ.get("PRISM_SOURCES_YAML")
    if override:
        return Path(override)
    from prism.config import settings
    return Path(getattr(settings, "source_config", ""))


def _source_already_present(hint: dict, yaml_path: Path) -> bool:
    if not yaml_path.exists():
        return False
    try:
        from prism.sources.yaml_editor import load_sources_list, _source_key
    except ImportError:
        return False
    items = load_sources_list(yaml_path)
    want = _source_key({"type": hint.get("type", ""),
                         **({"handle": hint["handle"]} if "handle" in hint else {}),
                         **({"url": hint["url"]} if "url" in hint else {})})
    return any(_source_key(item) == want for item in items)


def run_external_feed_consumer(conn: sqlite3.Connection) -> int:
    """Process all external_feeds with processed=0. Returns count processed."""
    rows = conn.execute(
        "SELECT id, url, user_note FROM external_feeds WHERE processed = 0"
    ).fetchall()

    yaml_path = _sources_yaml_path()
    n = 0
    for feed_id, url, note in rows:
        prompt = (
            f"【用户投喂的链接】\n{url}\n\n"
            f"【用户备注】\n{note or '(无)'}\n\n"
            f"请分析并输出 JSON。"
        )
        try:
            extracted = call_llm_json(prompt, system=_SYSTEM_PROMPT, max_tokens=1024)
        except Exception as exc:  # noqa: BLE001
            # Leave processed=0 so next run retries; note the error in extracted_json.
            # Own transaction so prior successful rows aren't affected.
            with conn:
                conn.execute(
                    "UPDATE external_feeds SET extracted_json = ? WHERE id = ?",
                    (json.dumps({"error": str(exc)}, ensure_ascii=False), feed_id),
                )
            continue

        # Each successful row is its own atomic unit: proposal (if any) + processed=1
        # commit together, so a later row failing can't leave this row half-applied.
        with conn:
            hint = extracted.get("source_hint") or {}
            has_locator = bool(hint.get("handle") or hint.get("url"))
            if hint and hint.get("type") and has_locator and not _source_already_present(hint, yaml_path):
                cfg: dict[str, Any] = {"type": hint["type"]}
                if "handle" in hint:
                    cfg["handle"] = hint["handle"]
                    cfg["depth"] = "thread"
                elif "url" in hint:
                    cfg["url"] = hint["url"]
                display = hint.get("display_name") or cfg.get("handle") or cfg.get("url") or "unknown"
                conn.execute(
                    "INSERT INTO source_proposals "
                    "(source_type, source_config_json, display_name, rationale, origin, origin_ref) "
                    "VALUES (?, ?, ?, ?, 'external_feed', ?)",
                    (cfg["type"], json.dumps(cfg, ensure_ascii=False),
                     display,
                     f"来自你投喂的链接：{extracted.get('summary_zh','')}",
                     str(feed_id)),
                )

            conn.execute(
                "UPDATE external_feeds SET processed = 1, extracted_json = ? WHERE id = ?",
                (json.dumps(extracted, ensure_ascii=False), feed_id),
            )
        n += 1

    return n
