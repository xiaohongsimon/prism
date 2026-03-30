"""Generate slides: LLM extracts key points (JSON) → server renders HTML template.

Fast mode: ~5-10s per signal (300 token output vs 8192).
Background worker processes queue continuously.
"""

import json
import logging
import re
import sqlite3
import threading
import time

from prism.pipeline.llm import call_llm_json

logger = logging.getLogger(__name__)

FAST_MODEL = "MiMo-V2-Flash-4bit"

EXTRACT_PROMPT = """从以下内容中提炼出核心信息摘要卡。

要求：
- 一句话核心论点（≤30字，直击要害）
- 3-5 个关键洞察（每条 15-30 字，有数据/案例/反直觉的优先）
- 一句话结论或行动建议（≤25字）
- 所有内容用中文

输出 JSON 格式：
{{"thesis": "核心论点", "insights": ["洞察1", "洞察2", "洞察3", "洞察4"], "conclusion": "结论/行动建议"}}

内容标题：{title}

正文：
{content}
"""

SLIDES_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{min-height:100%;font-family:'Inter',-apple-system,"PingFang SC",sans-serif;
  background:radial-gradient(ellipse at top center,#12122a 0%,#0a0a0f 55%);color:#ebebf0}}
.card{{max-width:680px;margin:24px auto;padding:28px 32px;
  background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.06);border-radius:16px}}
.thesis{{font-size:clamp(18px,3.5vw,24px);font-weight:800;letter-spacing:-.02em;line-height:1.35;
  margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid rgba(255,255,255,.06)}}
.thesis .hl{{color:#6366f1}}
.insights{{list-style:none;display:flex;flex-direction:column;gap:10px;margin-bottom:20px}}
.insights li{{display:flex;gap:12px;align-items:flex-start;padding:12px 16px;
  background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.04);
  border-left:3px solid #6366f1;border-radius:10px;font-size:14px;line-height:1.6;color:#c0c0d0}}
.insights li .num{{flex-shrink:0;width:22px;height:22px;border-radius:50%;
  background:rgba(99,102,241,.15);color:#a78bfa;font-size:11px;font-weight:700;
  display:flex;align-items:center;justify-content:center;margin-top:1px}}
.conclusion{{font-size:15px;color:#8b8b9e;padding:14px 16px;
  background:rgba(99,102,241,.04);border:1px solid rgba(99,102,241,.1);
  border-radius:10px;text-align:center;font-weight:500}}
.conclusion .arrow{{color:#6366f1;font-weight:700}}
@media(max-width:600px){{.card{{margin:12px;padding:20px 18px}}.insights li{{padding:10px 12px}}}}
</style>
</head>
<body>
{content_html}
</body>
</html>"""


def _render_slides_html(data: dict) -> str:
    """Render extracted insights into a single info-dense card."""
    thesis = data.get("thesis", "")
    insights = data.get("insights", [])
    conclusion = data.get("conclusion", "")

    items = "\n".join(
        f'<li><span class="num">{i+1}</span><span>{ins}</span></li>'
        for i, ins in enumerate(insights)
    )

    content = f"""<div class="card">
<div class="thesis">{thesis}</div>
<ul class="insights">{items}</ul>
<div class="conclusion"><span class="arrow">→</span> {conclusion}</div>
</div>"""

    return SLIDES_TEMPLATE.format(content_html=content)


def generate_slides_fast(conn: sqlite3.Connection, signal_id: int) -> str | None:
    """Fast template-based generation: LLM outputs JSON → server renders HTML."""
    # Check cache
    row = conn.execute(
        "SELECT html FROM signal_slides WHERE signal_id = ?", (signal_id,)
    ).fetchone()
    if row:
        return row["html"]

    # Get content
    signal = conn.execute(
        "SELECT s.id, s.summary, s.cluster_id, c.topic_label "
        "FROM signals s JOIN clusters c ON s.cluster_id = c.id WHERE s.id = ?",
        (signal_id,),
    ).fetchone()
    if not signal:
        return None

    transcript_row = conn.execute(
        "SELECT ri.body FROM raw_items ri "
        "JOIN cluster_items ci ON ci.raw_item_id = ri.id "
        "WHERE ci.cluster_id = ? AND LENGTH(ri.body) > 80 "
        "ORDER BY LENGTH(ri.body) DESC LIMIT 1",
        (signal["cluster_id"],),
    ).fetchone()

    content = transcript_row["body"] if transcript_row else signal["summary"]
    if len(content) < 80:
        return None

    title = signal["topic_label"]
    prompt = EXTRACT_PROMPT.format(title=title, content=content[:5000])

    try:
        result = call_llm_json(prompt, model=FAST_MODEL, timeout=60)
        if not result.get("thesis") or not result.get("insights"):
            return None

        html = _render_slides_html(result)

        conn.execute(
            "INSERT OR REPLACE INTO signal_slides (signal_id, html, model_id) VALUES (?, ?, ?)",
            (signal_id, html, FAST_MODEL + "+template"),
        )
        conn.commit()
        return html
    except Exception as exc:
        logger.error("Slides generation failed for signal %d: %s", signal_id, exc)
        return None


def get_or_generate_slides(conn: sqlite3.Connection, signal_id: int) -> str | None:
    """Get cached slides or generate on demand."""
    row = conn.execute(
        "SELECT html FROM signal_slides WHERE signal_id = ?", (signal_id,)
    ).fetchone()
    if row:
        return row["html"]
    return generate_slides_fast(conn, signal_id)


# --- Background Worker ---

_worker_running = False
_worker_lock = threading.Lock()


def start_slides_worker(conn: sqlite3.Connection, batch_size: int = 10, interval: int = 30):
    """Start background thread that continuously generates slides for pending signals."""
    global _worker_running
    with _worker_lock:
        if _worker_running:
            return
        _worker_running = True

    def _worker():
        global _worker_running
        logger.info("Slides background worker started")
        while _worker_running:
            try:
                # Round-robin: pick from each source type to ensure coverage
                rows = conn.execute(
                    """
                    SELECT s.id as signal_id, src.type,
                           ROW_NUMBER() OVER (PARTITION BY src.type ORDER BY s.signal_strength DESC) as rn
                    FROM signals s
                    JOIN clusters c ON c.id = s.cluster_id
                    JOIN cluster_items ci ON ci.cluster_id = c.id
                    JOIN raw_items ri ON ri.id = ci.raw_item_id
                    JOIN sources src ON src.id = ri.source_id
                    WHERE s.is_current = 1 AND LENGTH(ri.body) > 80
                      AND s.id NOT IN (SELECT signal_id FROM signal_slides WHERE signal_id > 0)
                    ORDER BY rn, s.signal_strength DESC
                    LIMIT ?
                    """,
                    (batch_size,),
                ).fetchall()

                if not rows:
                    time.sleep(interval)
                    continue

                for row in rows:
                    if not _worker_running:
                        break
                    generate_slides_fast(conn, row["signal_id"])

            except Exception as exc:
                logger.error("Slides worker error: %s", exc)
                time.sleep(interval)

        logger.info("Slides background worker stopped")

    t = threading.Thread(target=_worker, daemon=True, name="slides-worker")
    t.start()


def stop_slides_worker():
    global _worker_running
    _worker_running = False
