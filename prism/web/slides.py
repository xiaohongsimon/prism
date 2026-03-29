"""Generate presentation-quality HTML slides from video transcripts.

Flow: full transcript → content planning (MiMo) → HTML generation (Qwen Coder)
"""

import logging
import sqlite3

from prism.pipeline.llm import call_llm

logger = logging.getLogger(__name__)

# Step 1: Content planning — extract the essence from full transcript
PLAN_MODEL = "MiMo-V2-Flash-4bit"

PLAN_PROMPT = """你是一个顶级内容策划师。从以下视频字幕全文中，提炼出一份 5 页 PPT 的内容大纲。

要求：
- 第 1 页：标题页（一句话标题 + 副标题）
- 第 2-4 页：每页一个最震撼/最有价值的核心观点
- 第 5 页：一句话总结 / 行动号召
- 每页的核心观点用一句话（≤20字）表达，下方补充 1-2 句支撑论据
- 优先选择有数据、有案例、有反直觉的观点
- 输出纯文本大纲，格式如下：

页1标题: ...
页1副标题: ...
页2标题: ...
页2要点: ...
页3标题: ...
页3要点: ...
页4标题: ...
页4要点: ...
页5总结: ...

视频标题：{title}

字幕全文：
{transcript}
"""

# Step 2: HTML generation — turn outline into beautiful slides
CODE_MODEL = "Qwen3-Coder-Next-MLX-8bit"

CODE_PROMPT = """根据以下 PPT 大纲，生成一个自包含的 HTML 幻灯片。

设计要求：
- 深色背景渐变 (从 #0a0a0a 到 #1a1a2e)
- 主标题：白色大字，36-48px，font-weight 700
- 副文字：#aaa，18-20px
- 强调色：#1d9bf0（蓝），用于关键词高亮
- 每页全屏展示，居中排版，大量留白
- 左右箭头键切换，底部圆点导航
- 字体：-apple-system, "PingFang SC", sans-serif
- 过渡动画：淡入淡出 0.3s
- 响应式：在手机和桌面都好看
- 宽度 100%，高度 100vh（全屏 slides）
- 整个 HTML 完全自包含（inline CSS + JS），不依赖外部资源

只输出 HTML 代码，不要任何解释。

PPT 大纲：
{outline}
"""


def get_or_generate_slides(conn: sqlite3.Connection, signal_id: int) -> str | None:
    """Get cached slides or generate new ones from full transcript."""
    # Check cache
    row = conn.execute(
        "SELECT html FROM signal_slides WHERE signal_id = ?", (signal_id,)
    ).fetchone()
    if row:
        return row["html"]

    # Get signal + cluster + full transcript
    signal = conn.execute(
        "SELECT s.id, s.summary, s.cluster_id, c.topic_label "
        "FROM signals s JOIN clusters c ON s.cluster_id = c.id "
        "WHERE s.id = ?",
        (signal_id,),
    ).fetchone()
    if not signal:
        return None

    # Get full transcript from raw_items (the enriched subtitle text)
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

    # Step 1: Content planning from full transcript
    logger.info("Planning slides for signal %d: %s", signal_id, title[:50])
    plan_prompt = PLAN_PROMPT.format(title=title, transcript=transcript[:6000])
    try:
        outline = call_llm(plan_prompt, model=PLAN_MODEL, timeout=120)
    except Exception as exc:
        logger.error("Slides planning failed for signal %d: %s", signal_id, exc)
        return None

    # Step 2: Generate HTML from outline (needs high max_tokens for full HTML+CSS+JS)
    logger.info("Generating HTML slides for signal %d", signal_id)
    code_prompt = CODE_PROMPT.format(outline=outline)
    try:
        html = call_llm(code_prompt, model=CODE_MODEL, timeout=300, max_tokens=8192)
    except Exception as exc:
        logger.error("Slides HTML generation failed for signal %d: %s", signal_id, exc)
        return None

    # Clean response
    html = html.strip()
    # Strip <think> tags from reasoning models
    import re
    html = re.sub(r"<think>.*?</think>", "", html, flags=re.DOTALL).strip()
    if "```html" in html:
        html = html.split("```html", 1)[1]
    if "```" in html:
        html = html.split("```")[0]
    html = html.strip()

    if not html.startswith("<!") and not html.startswith("<html"):
        # Try to find the HTML start
        idx = html.find("<!DOCTYPE") or html.find("<html")
        if idx and idx > 0:
            html = html[idx:]
        else:
            logger.error("Generated content is not valid HTML for signal %d", signal_id)
            return None

    # Cache
    conn.execute(
        "INSERT OR REPLACE INTO signal_slides (signal_id, html, model_id) VALUES (?, ?, ?)",
        (signal_id, html, f"{PLAN_MODEL}+{CODE_MODEL}"),
    )
    conn.commit()
    logger.info("Slides cached for signal %d (%d bytes)", signal_id, len(html))
    return html
