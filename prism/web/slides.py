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

EXTRACT_PROMPT = """从以下内容中提炼出 5 页 PPT 的核心要点。

要求：
- 第 1 页：标题页（一句标题 ≤15字 + 一句副标题 ≤25字）
- 第 2-4 页：每页一个最精华的观点（标题 ≤12字 + 补充说明 ≤40字）
- 第 5 页：一句话总结或行动号召 ≤20字
- 优先选择有数据、有案例、有反直觉的观点
- 所有内容用中文

输出 JSON 格式：
{{"slides": [
  {{"title": "...", "subtitle": "..."}},
  {{"title": "...", "body": "..."}},
  {{"title": "...", "body": "..."}},
  {{"title": "...", "body": "..."}},
  {{"title": "...", "subtitle": ""}}
]}}

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
html,body{{height:100%;overflow:hidden;font-family:'Inter',-apple-system,"PingFang SC",sans-serif;
  background:radial-gradient(ellipse at top center,#12122a 0%,#0a0a0f 55%);color:#ebebf0}}
.slide{{position:absolute;top:0;left:0;width:100%;height:100%;display:flex;flex-direction:column;
  justify-content:center;align-items:center;padding:48px;opacity:0;transition:opacity .3s ease;pointer-events:none;text-align:center}}
.slide.active{{opacity:1;pointer-events:auto}}
.slide h1{{font-size:clamp(28px,5vw,44px);font-weight:800;letter-spacing:-.02em;line-height:1.2;margin-bottom:24px;max-width:85%}}
.slide .sub{{font-size:clamp(16px,2.5vw,20px);color:#8b8b9e;line-height:1.6;max-width:75%}}
.slide .body-card{{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);
  border-left:3px solid #6366f1;border-radius:12px;padding:24px 32px;max-width:700px;text-align:left;margin-top:8px}}
.slide .body-card p{{font-size:clamp(15px,2vw,18px);color:#8b8b9e;line-height:1.7}}
.hl{{color:#6366f1;font-weight:600}}
.dots{{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);display:flex;gap:10px;z-index:10}}
.dot{{width:8px;height:8px;border-radius:50%;background:rgba(255,255,255,.2);cursor:pointer;transition:all .2s}}
.dot.active{{background:#6366f1;transform:scale(1.3)}}
.hint{{position:fixed;bottom:8px;width:100%;text-align:center;color:rgba(255,255,255,.25);font-size:12px}}
@media(max-width:768px){{.slide{{padding:24px}}.slide .body-card{{padding:16px 20px}}}}
</style>
</head>
<body>
{slides_html}
<div class="dots">{dots_html}</div>
<div class="hint">← → 翻页</div>
<script>
const S=document.querySelectorAll('.slide'),D=document.querySelectorAll('.dot');
let c=0;
function go(i){{if(i<0||i>=S.length)return;S[c].classList.remove('active');D[c].classList.remove('active');
c=i;S[c].classList.add('active');D[c].classList.add('active')}}
document.addEventListener('keydown',e=>{{if(e.key==='ArrowRight')go(c+1);if(e.key==='ArrowLeft')go(c-1)}});
D.forEach((d,i)=>d.onclick=()=>go(i));
</script>
</body>
</html>"""


def _render_slides_html(slides_data: list[dict]) -> str:
    """Render slides JSON into complete HTML."""
    slides_parts = []
    for i, s in enumerate(slides_data):
        active = ' active' if i == 0 else ''
        title = s.get("title", "")
        subtitle = s.get("subtitle", "")
        body = s.get("body", "")

        if subtitle:
            slides_parts.append(
                f'<div class="slide{active}"><h1>{title}</h1><div class="sub">{subtitle}</div></div>'
            )
        elif body:
            slides_parts.append(
                f'<div class="slide{active}"><h1>{title}</h1><div class="body-card"><p>{body}</p></div></div>'
            )
        else:
            slides_parts.append(
                f'<div class="slide{active}"><h1>{title}</h1></div>'
            )

    dots = "".join(
        f'<div class="dot{" active" if i == 0 else ""}"></div>'
        for i in range(len(slides_data))
    )
    return SLIDES_TEMPLATE.format(slides_html="\n".join(slides_parts), dots_html=dots)


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
        "WHERE ci.cluster_id = ? AND LENGTH(ri.body) > 500 "
        "ORDER BY LENGTH(ri.body) DESC LIMIT 1",
        (signal["cluster_id"],),
    ).fetchone()

    content = transcript_row["body"] if transcript_row else signal["summary"]
    if len(content) < 100:
        return None

    title = signal["topic_label"]
    prompt = EXTRACT_PROMPT.format(title=title, content=content[:5000])

    try:
        result = call_llm_json(prompt, model=FAST_MODEL, timeout=60)
        slides_data = result.get("slides", [])
        if not slides_data or len(slides_data) < 3:
            return None

        html = _render_slides_html(slides_data)

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
                rows = conn.execute(
                    """
                    SELECT DISTINCT s.id as signal_id
                    FROM signals s
                    JOIN clusters c ON c.id = s.cluster_id
                    JOIN cluster_items ci ON ci.cluster_id = c.id
                    JOIN raw_items ri ON ri.id = ci.raw_item_id
                    WHERE s.is_current = 1 AND LENGTH(ri.body) > 500
                      AND s.id NOT IN (SELECT signal_id FROM signal_slides WHERE signal_id > 0)
                    ORDER BY s.signal_strength DESC
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
