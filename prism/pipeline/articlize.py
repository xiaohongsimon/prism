"""Video-to-article pipeline: convert YouTube subtitles into structured articles."""

import json
import re
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

logger = logging.getLogger(__name__)

MAX_BODY_LENGTH = 8000  # YouTube adapter caps at 8000 chars

ARTICLIZE_SYSTEM = """你是一个专业的内容编辑。将视频字幕转化为结构化文章。"""

ARTICLIZE_USER_TEMPLATE = """将以下视频字幕转化为结构化文章。

视频标题: {title}

字幕原文:
{body}

要求:
1. 提取 3-5 个核心章节，每个章节有标题和正文
2. 用 **粗体** 标注关键洞察和数据点
3. 提取 3-5 条最有价值的原始引用（用 > 引用格式）
4. 写一句话摘要（subtitle）
5. 去除口语化填充词、重复内容、无关闲聊
6. 保留原始观点和论证逻辑，不要添加评论

输出 JSON（不要输出其他内容）:
{{"subtitle": "一句话摘要", "body": "Markdown 正文", "highlights": ["关键引用1", "关键引用2"]}}"""


def find_eligible_items(conn: sqlite3.Connection) -> list[dict]:
    """Find YouTube raw_items that need article generation.

    Conditions:
    - source type in ('youtube', 'youtube_home')
    - body is not empty and length <= MAX_BODY_LENGTH
    - no existing article for this raw_item
    """
    rows = conn.execute(
        """
        SELECT ri.id, ri.title, ri.body, ri.url, ri.author, s.source_key
        FROM raw_items ri
        JOIN sources s ON ri.source_id = s.id
        LEFT JOIN articles a ON a.raw_item_id = ri.id
        WHERE s.type IN ('youtube', 'youtube_home')
          AND length(ri.body) > 0
          AND length(ri.body) <= ?
          AND a.id IS NULL
        ORDER BY ri.created_at DESC
        """,
        (MAX_BODY_LENGTH,),
    ).fetchall()
    return [dict(r) for r in rows]


def parse_llm_response(raw: str) -> dict | None:
    """Extract and validate JSON from LLM response.

    Handles: raw JSON, ```json wrapped, thinking tags.
    Returns parsed dict or None if invalid.
    """
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    # Try direct parse first
    try:
        result = json.loads(text)
        if _validate_article(result):
            return result
    except json.JSONDecodeError:
        pass

    # Try extracting ```json block
    m = re.search(r"```json\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group(1))
            if _validate_article(result):
                return result
        except json.JSONDecodeError:
            pass

    # Try finding first {...} block
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group(0))
            if _validate_article(result):
                return result
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse LLM response as valid article JSON")
    return None


_PLACEHOLDER_SUBTITLES = {"...", "一句话摘要"}
_PLACEHOLDER_BODY_MARKERS = (
    "Markdown 正文",
    "Markdown 格式的正文",
    "章节标题",
    "正文内容",
    "关键引用1",
)


def _validate_article(data: dict) -> bool:
    """Reject empty or placeholder articles so they never reach readers.

    Guards against the failure mode where the LLM echoes the prompt's JSON
    example verbatim (e.g. subtitle='...', body='# 章节1\\n...'). Rejected
    articles leave the raw_item eligible for a retry on the next run.
    """
    if not isinstance(data, dict):
        return False
    subtitle = (data.get("subtitle") or "").strip()
    body = (data.get("body") or "").strip()
    if not subtitle or not body:
        return False
    if subtitle in _PLACEHOLDER_SUBTITLES:
        return False
    if any(marker in body for marker in _PLACEHOLDER_BODY_MARKERS):
        return False
    # A body whose only content under headings is `...` (possibly repeated)
    # is a placeholder dump — strip headings and see what's left.
    stripped = re.sub(r"(?m)^\s*#+\s.*$", "", body).strip()
    if stripped and all(line.strip() in {"", "..."} for line in stripped.splitlines()):
        return False
    return True


def save_article(
    conn: sqlite3.Connection,
    *,
    raw_item_id: int,
    title: str,
    subtitle: str,
    structured_body: str,
    highlights: list[str],
    model_id: str,
) -> int:
    """Insert article into DB. Returns article id."""
    word_count = len(structured_body)
    cursor = conn.execute(
        """INSERT INTO articles (raw_item_id, title, subtitle, structured_body,
           highlights_json, word_count, model_id, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            raw_item_id,
            title,
            subtitle,
            structured_body,
            json.dumps(highlights, ensure_ascii=False),
            word_count,
            model_id,
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    return cursor.lastrowid


def _articlize_one(item: dict) -> tuple[int, str, dict | None, str | None]:
    """Worker: LLM call only, no DB access. Returns (item_id, title, parsed, error)."""
    from prism.pipeline.llm import call_llm_json
    from prism.pipeline.llm_tasks import Scope, Task

    prompt = ARTICLIZE_USER_TEMPLATE.format(title=item["title"], body=item["body"])
    try:
        raw_response = call_llm_json(prompt, system=ARTICLIZE_SYSTEM, max_tokens=4096,
                                     task=Task.STRUCTURIZE, scope=Scope.ITEM)
        if isinstance(raw_response, dict) and _validate_article(raw_response):
            parsed = raw_response
        else:
            parsed = parse_llm_response(
                json.dumps(raw_response) if isinstance(raw_response, dict) else str(raw_response)
            )
    except Exception as exc:
        return item["id"], item["title"], None, f"{type(exc).__name__}: {exc}"
    return item["id"], item["title"], parsed, None


def run_articlize(conn: sqlite3.Connection, *, max_workers: int = 4) -> dict:
    """Main entry point: find eligible items and generate articles.

    Parallelism: `max_workers` threads run LLM calls concurrently. DB writes
    stay on the main thread (sqlite3 connection is not thread-safe for
    concurrent writes).
    """
    items = find_eligible_items(conn)
    logger.info("Found %d eligible items for articlize", len(items))

    stats = {"total": len(items), "success": 0, "failed": 0, "skipped": 0}
    if not items:
        return stats

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_articlize_one, item) for item in items]
        for future in as_completed(futures):
            item_id, title, parsed, error = future.result()

            if error:
                logger.warning("LLM call failed for item %d (%s): %s", item_id, title, error)
                stats["failed"] += 1
                continue

            if not parsed:
                logger.warning("Invalid LLM response for item %d (%s)", item_id, title)
                stats["failed"] += 1
                continue

            save_article(
                conn,
                raw_item_id=item_id,
                title=title,
                subtitle=parsed["subtitle"],
                structured_body=parsed["body"],
                highlights=parsed.get("highlights", []),
                model_id="omlx",
            )
            stats["success"] += 1
            logger.info("Generated article for: %s", title)

    return stats
