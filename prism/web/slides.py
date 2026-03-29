"""Generate presentation-quality HTML slides via multi-model horse race.

Flow:
1. Full transcript → content outline (each contestant model independently)
2. Outline → HTML slides (each contestant generates its own)
3. Opus judges all entries, picks top 2
4. Winners stored and displayed on site
"""

import json
import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from prism.pipeline.llm import call_llm

logger = logging.getLogger(__name__)

# --- Contestant models (local) ---
CONTESTANTS = [
    "GLM-4.7-Flash-MLX-8bit",
    "MiMo-V2-Flash-4bit",
    "Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled-qx64-hi-mlx",
]

# --- Judge ---
JUDGE_MODEL = "Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled-qx64-hi-mlx"

# --- Prompts ---
GENERATE_PROMPT = """你是一个顶级演讲设计师。根据以下视频字幕全文，生成一个 5 页自包含 HTML 幻灯片。

设计原则：
- 字要少！每页核心观点 ≤15 个字，大字体居中
- 下方用 1-2 句小字补充关键论据或数据
- 提炼最震撼、最有价值、最反直觉的观点
- 视觉要高级：深色渐变背景、大量留白、蓝色强调

技术要求：
- 完全自包含的 HTML（inline CSS + JS）
- 深色背景渐变 (#0a0a0a → #1a1a2e)
- 主标题白色 40-48px，补充文字 #aaa 18px
- 强调色 #1d9bf0，关键词高亮
- 字体：-apple-system, "PingFang SC", sans-serif
- 左右箭头键 + 底部圆点导航切换
- 淡入淡出过渡动画
- 全屏 100vh slides，响应式

第 1 页：标题页（一句话核心主题 + 短副标题）
第 2-4 页：每页一个最精华的观点（大字 + 小字论据）
第 5 页：一句话总结或行动号召

只输出 HTML 代码，不要任何解释文字。

视频标题：{title}

字幕全文：
{transcript}
"""

JUDGE_PROMPT = """你是一位严格的演讲评审。以下是 {n} 份由不同 AI 模型生成的 HTML 幻灯片，内容来自同一个视频。

请评估每份作品，从以下维度打分（1-10）：
1. 内容提炼：是否抓住了视频最核心、最有价值的观点？
2. 信息密度：字数是否精炼？是否做到"少即是多"？
3. 视觉设计：排版、配色、留白是否高级？
4. 可分享性：拿去分享给别人，对方能否快速理解并留下深刻印象？

输出 JSON 格式：
{{
  "rankings": [
    {{"entry": 1, "total_score": 35, "content": 9, "density": 8, "visual": 9, "shareable": 9, "comment": "..."}},
    ...
  ],
  "winners": [1, 3],
  "reason": "选择理由"
}}

其中 winners 是得分最高的两个 entry 编号（从 1 开始）。

{entries}
"""


def _generate_one(model: str, title: str, transcript: str) -> str | None:
    """Single contestant generates slides."""
    prompt = GENERATE_PROMPT.format(title=title, transcript=transcript[:6000])
    try:
        html = call_llm(prompt, model=model, timeout=300, max_tokens=8192)
        html = html.strip()
        # Clean reasoning tags
        html = re.sub(r"<think>.*?</think>", "", html, flags=re.DOTALL).strip()
        if "```html" in html:
            html = html.split("```html", 1)[1]
        if "```" in html:
            html = html.split("```")[0]
        html = html.strip()
        # Validate
        if "</html>" in html and "slide" in html:
            return html
        return None
    except Exception as exc:
        logger.error("Contestant %s failed: %s", model, exc)
        return None


def _judge_entries(entries: list[tuple[str, str]], title: str) -> list[int]:
    """Judge picks top 2 entries. Returns list of winning indices (0-based)."""
    if len(entries) <= 2:
        return list(range(len(entries)))

    # Build entries text for judge (abbreviated — just structure, not full HTML)
    entry_texts = []
    for i, (model, html) in enumerate(entries, 1):
        # Extract visible text content for judging
        text_only = re.sub(r"<style>.*?</style>", "", html, flags=re.DOTALL)
        text_only = re.sub(r"<script>.*?</script>", "", text_only, flags=re.DOTALL)
        text_only = re.sub(r"<[^>]+>", " ", text_only)
        text_only = re.sub(r"\s+", " ", text_only).strip()
        entry_texts.append(f"=== Entry {i} (by {model}) ===\n{text_only[:1500]}\n")

    prompt = JUDGE_PROMPT.format(n=len(entries), entries="\n".join(entry_texts))
    try:
        from prism.pipeline.llm import call_llm_json
        result = call_llm_json(prompt, model=JUDGE_MODEL, timeout=120)
        winners = result.get("winners", [1, 2])
        logger.info("Judge result: winners=%s, reason=%s", winners, result.get("reason", ""))
        return [w - 1 for w in winners]  # Convert to 0-based
    except Exception as exc:
        logger.error("Judge failed: %s, defaulting to first two", exc)
        return [0, 1]


def get_or_generate_slides(conn: sqlite3.Connection, signal_id: int) -> str | None:
    """Get cached slides or run horse race to generate."""
    # Check cache
    row = conn.execute(
        "SELECT html FROM signal_slides WHERE signal_id = ?", (signal_id,)
    ).fetchone()
    if row:
        return row["html"]

    # Get signal + full transcript
    signal = conn.execute(
        "SELECT s.id, s.summary, s.cluster_id, c.topic_label "
        "FROM signals s JOIN clusters c ON s.cluster_id = c.id "
        "WHERE s.id = ?",
        (signal_id,),
    ).fetchone()
    if not signal:
        return None

    transcript_row = conn.execute(
        """
        SELECT ri.body, ri.title FROM raw_items ri
        JOIN cluster_items ci ON ci.raw_item_id = ri.id
        WHERE ci.cluster_id = ? AND LENGTH(ri.body) > 500
        ORDER BY LENGTH(ri.body) DESC LIMIT 1
        """,
        (signal["cluster_id"],),
    ).fetchone()
    if not transcript_row:
        return None

    transcript = transcript_row["body"]
    title = signal["topic_label"]

    # --- Horse Race: all contestants generate in parallel ---
    logger.info("Starting slides horse race for signal %d: %s", signal_id, title[:50])
    entries: list[tuple[str, str]] = []  # (model, html)

    with ThreadPoolExecutor(max_workers=len(CONTESTANTS)) as executor:
        futures = {
            executor.submit(_generate_one, model, title, transcript): model
            for model in CONTESTANTS
        }
        for future in futures:
            model = futures[future]
            html = future.result()
            if html:
                entries.append((model, html))
                logger.info("Contestant %s: ✓ (%d bytes)", model, len(html))
            else:
                logger.warning("Contestant %s: ✗ failed", model)

    if not entries:
        logger.error("All contestants failed for signal %d", signal_id)
        return None

    if len(entries) == 1:
        winner_html = entries[0][1]
        winner_model = entries[0][0]
    else:
        # --- Judge picks top 2 ---
        logger.info("Judging %d entries...", len(entries))
        winner_indices = _judge_entries(entries, title)

        # Store both winners
        for rank, idx in enumerate(winner_indices[:2]):
            if idx < len(entries):
                model, html = entries[idx]
                suffix = "" if rank == 0 else "_runner_up"
                conn.execute(
                    f"INSERT OR REPLACE INTO signal_slides (signal_id, html, model_id) VALUES (?, ?, ?)",
                    (signal_id if rank == 0 else -signal_id, html, model),
                )
                logger.info("Winner #%d: %s", rank + 1, model)

        winner_idx = winner_indices[0] if winner_indices[0] < len(entries) else 0
        winner_html = entries[winner_idx][1]
        winner_model = entries[winner_idx][0]

    # Cache primary winner
    conn.execute(
        "INSERT OR REPLACE INTO signal_slides (signal_id, html, model_id) VALUES (?, ?, ?)",
        (signal_id, winner_html, winner_model),
    )
    conn.commit()
    logger.info("Horse race complete for signal %d. Winner: %s", signal_id, winner_model)
    return winner_html
