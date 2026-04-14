"""Generate slides: LLM extracts key points (JSON) → server renders HTML template.

Fast mode: ~5-10s per signal (300 token output vs 8192).
Background worker processes queue continuously.
"""

import json
import logging
import os
import re
import sqlite3
import threading
import time

from prism.pipeline.llm import call_llm_json

logger = logging.getLogger(__name__)

FAST_MODEL = os.getenv("PRISM_LLM_MODEL", "gemma-4-31b-it-8bit")

EXTRACT_PROMPT = """分析以下内容，选择最合适的可视化格式来展示核心信息。

可选格式（严格根据内容类型选择，不要总选 debate）：
1. "debate" — 仅当内容有明确的两方立场对立时使用（如政策争议、技术路线之争）
2. "metrics" — 有具体数字/性能指标时使用（技术发布、评测报告、产品更新、融资）
3. "timeline" — 内容涉及多个时间节点/阶段演进时使用（行业回顾、个人经历、版本迭代）
4. "quote" — 有一个核心人物发表观点/金句时使用（访谈、演讲、个人观点推文）
5. "method" — 有明确问题→方案→效果结构时使用（论文、技术方案、开源项目介绍）

选择标准：先判断内容是否有数据（→metrics）、是否有时间线（→timeline）、是否有核心人物金句（→quote）、是否有问题→方案（→method），都不是才考虑 debate。

根据你选的格式，输出对应的 JSON：

debate 格式：
{{"format": "debate", "topic": "争论主题≤15字", "for": [{{"point": "支持观点≤25字", "evidence": "论据≤30字"}}], "against": [{{"point": "反对观点≤25字", "evidence": "论据≤30字"}}], "verdict": "结论≤25字"}}

metrics 格式：
{{"format": "metrics", "title": "产品/技术名≤15字", "subtitle": "一句话定位≤20字", "metrics": [{{"label": "指标名≤6字", "value": "数值", "delta": "+20%或描述", "good": true}}], "takeaway": "一句话结论≤25字"}}

timeline 格式：
{{"format": "timeline", "title": "主题≤15字", "events": [{{"time": "时间点", "text": "事件描述≤25字", "highlight": false}}], "insight": "演进规律≤30字"}}

quote 格式：
{{"format": "quote", "speaker": "人名", "role": "身份≤15字", "quote": "最精华的一句话≤40字", "context": "背景说明≤50字", "implications": ["启示1≤25字", "启示2≤25字", "启示3≤25字"]}}

method 格式：
{{"format": "method", "problem": "要解决的问题≤25字", "approach": "核心方法≤30字", "results": [{{"metric": "指标", "value": "结果"}}], "significance": "意义≤30字"}}

内容标题：{title}

正文：
{content}
"""

CARD_CSS = """*{margin:0;padding:0;box-sizing:border-box}
html,body{min-height:100%;font-family:'Outfit','PingFang SC',system-ui,sans-serif;
  background:radial-gradient(ellipse at top center,#10101e 0%,#0a0a0f 55%);color:#ebebf0;font-size:14px}
.c{max-width:680px;margin:20px auto;padding:24px 28px;
  background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.06);border-radius:16px}
.hd{font-size:clamp(16px,3vw,20px);font-weight:600;letter-spacing:-.02em;line-height:1.3;
  font-family:'Noto Serif SC','Songti SC',Georgia,serif;
  margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid rgba(255,255,255,.06)}
.hl{color:#6366f1}.gr{color:#34d399}.rd{color:#f87171}.yl{color:#fbbf24}
.sub{font-size:13px;color:#8b8b9e;margin-bottom:14px}
/* debate */
.vs{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px}
.vs-col{padding:14px;border-radius:10px;font-size:13px;line-height:1.6}
.vs-for{background:rgba(52,211,153,.04);border:1px solid rgba(52,211,153,.12)}
.vs-against{background:rgba(248,113,113,.04);border:1px solid rgba(248,113,113,.12)}
.vs-col h3{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px}
.vs-for h3{color:#34d399}.vs-against h3{color:#f87171}
.vs-item{margin-bottom:8px}.vs-item strong{color:#c0c0d0}
.vs-item span{display:block;color:#6a6a7e;font-size:12px;margin-top:2px}
/* metrics */
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:14px}
.m{padding:14px;background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.04);border-radius:10px;text-align:center}
.m .val{font-size:22px;font-weight:800;color:#ebebf0;margin-bottom:2px}
.m .delta{font-size:12px;font-weight:600;margin-bottom:4px}
.m .delta.up{color:#34d399}.m .delta.down{color:#f87171}.m .delta.neutral{color:#8b8b9e}
.m .lbl{font-size:11px;color:#5a5a6e;text-transform:uppercase;letter-spacing:.5px}
/* timeline */
.tl{position:relative;padding-left:20px;margin-bottom:14px}
.tl::before{content:'';position:absolute;left:6px;top:4px;bottom:4px;width:2px;background:rgba(99,102,241,.2);border-radius:1px}
.tl-item{position:relative;padding:8px 0 8px 16px;font-size:13px;line-height:1.5}
.tl-item::before{content:'';position:absolute;left:-17px;top:14px;width:8px;height:8px;border-radius:50%;
  background:rgba(99,102,241,.3);border:2px solid #0a0a0f}
.tl-item.hi::before{background:#6366f1;box-shadow:0 0 8px rgba(99,102,241,.4)}
.tl-time{font-size:11px;color:#5a5a6e;font-weight:600;margin-bottom:2px}
.tl-text{color:#c0c0d0}
/* quote */
.qt{padding:20px 24px;background:rgba(99,102,241,.04);border-left:3px solid #6366f1;
  border-radius:0 12px 12px 0;margin-bottom:14px;font-size:16px;font-weight:500;
  line-height:1.6;color:#ebebf0;font-style:italic;font-family:'Noto Serif SC','Songti SC',Georgia,serif}
.qt-src{font-size:12px;color:#8b8b9e;font-style:normal;margin-top:8px;font-weight:400}
.imp{list-style:none;display:flex;flex-direction:column;gap:6px;margin-bottom:14px}
.imp li{padding:8px 14px;background:rgba(255,255,255,.02);border:1px solid rgba(255,255,255,.04);
  border-left:3px solid #a78bfa;border-radius:8px;font-size:13px;color:#c0c0d0;line-height:1.5}
/* method */
.method-flow{display:flex;flex-direction:column;gap:8px;margin-bottom:14px}
.mf-step{padding:12px 16px;border-radius:10px;font-size:13px;line-height:1.5}
.mf-problem{background:rgba(248,113,113,.04);border:1px solid rgba(248,113,113,.1);color:#c0c0d0}
.mf-approach{background:rgba(99,102,241,.04);border:1px solid rgba(99,102,241,.1);color:#c0c0d0}
.mf-results{background:rgba(52,211,153,.04);border:1px solid rgba(52,211,153,.1);color:#c0c0d0}
.mf-step .tag{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;margin-bottom:4px;display:block}
.mf-problem .tag{color:#f87171}.mf-approach .tag{color:#6366f1}.mf-results .tag{color:#34d399}
.res-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:6px;margin-top:6px}
.res-item{font-size:12px;color:#8b8b9e}.res-item strong{color:#34d399;font-size:14px;display:block}
/* footer */
.ft{font-size:13px;color:#8b8b9e;padding:12px 14px;background:rgba(99,102,241,.03);
  border:1px solid rgba(99,102,241,.08);border-radius:8px;text-align:center;font-weight:500}
.ft .arrow{color:#6366f1;font-weight:700}
@media(max-width:600px){.c{margin:8px;padding:16px}.vs{grid-template-columns:1fr}
  .metrics{grid-template-columns:1fr 1fr}}"""

_CARD_HEAD = '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>' + CARD_CSS + '</style></head><body>'
_CARD_TAIL = '</body></html>'


def _wrap_card(inner_html: str) -> str:
    return _CARD_HEAD + inner_html + _CARD_TAIL


def _render_visual(data: dict) -> str:
    """Render structured data into the appropriate visual format."""
    fmt = data.get("format", "quote")
    html = ""

    if fmt == "debate":
        topic = data.get("topic", "")
        fors = data.get("for", [])
        againsts = data.get("against", [])
        verdict = data.get("verdict", "")
        for_items = "".join(f'<div class="vs-item"><strong>{p["point"]}</strong><span>{p.get("evidence","")}</span></div>' for p in fors)
        against_items = "".join(f'<div class="vs-item"><strong>{p["point"]}</strong><span>{p.get("evidence","")}</span></div>' for p in againsts)
        html = f"""<div class="c">
<div class="hd">{topic}</div>
<div class="vs">
<div class="vs-col vs-for"><h3>支持 ✓</h3>{for_items}</div>
<div class="vs-col vs-against"><h3>反对 ✗</h3>{against_items}</div>
</div>
<div class="ft"><span class="arrow">→</span> {verdict}</div></div>"""

    elif fmt == "metrics":
        title = data.get("title", "")
        subtitle = data.get("subtitle", "")
        metrics = data.get("metrics", [])
        takeaway = data.get("takeaway", "")
        m_html = ""
        for m in metrics:
            good = m.get("good", True)
            cls = "up" if good else "down"
            m_html += f'<div class="m"><div class="val">{m.get("value","")}</div><div class="delta {cls}">{m.get("delta","")}</div><div class="lbl">{m.get("label","")}</div></div>'
        html = f"""<div class="c">
<div class="hd">{title}</div><div class="sub">{subtitle}</div>
<div class="metrics">{m_html}</div>
<div class="ft"><span class="arrow">→</span> {takeaway}</div></div>"""

    elif fmt == "timeline":
        title = data.get("title", "")
        events = data.get("events", [])
        insight = data.get("insight", "")
        ev_html = ""
        for ev in events:
            hi = " hi" if ev.get("highlight") else ""
            ev_html += f'<div class="tl-item{hi}"><div class="tl-time">{ev.get("time","")}</div><div class="tl-text">{ev.get("text","")}</div></div>'
        html = f"""<div class="c">
<div class="hd">{title}</div>
<div class="tl">{ev_html}</div>
<div class="ft"><span class="arrow">→</span> {insight}</div></div>"""

    elif fmt == "quote":
        speaker = data.get("speaker", "")
        role = data.get("role", "")
        quote = data.get("quote", "")
        context = data.get("context", "")
        implications = data.get("implications", [])
        imp_html = "".join(f"<li>{imp}</li>" for imp in implications)
        html = f"""<div class="c">
<div class="qt">"{quote}"<div class="qt-src">— {speaker}，{role}</div></div>
<div class="sub">{context}</div>
<ul class="imp">{imp_html}</ul></div>"""

    elif fmt == "method":
        problem = data.get("problem", "")
        approach = data.get("approach", "")
        results = data.get("results", [])
        significance = data.get("significance", "")
        res_html = "".join(
            f'<div class="res-item"><strong>{r.get("value","") if isinstance(r, dict) else r}</strong>{r.get("metric","") if isinstance(r, dict) else ""}</div>'
            for r in results
        )
        html = f"""<div class="c">
<div class="hd">研究速览</div>
<div class="method-flow">
<div class="mf-step mf-problem"><span class="tag">问题</span>{problem}</div>
<div class="mf-step mf-approach"><span class="tag">方法</span>{approach}</div>
<div class="mf-step mf-results"><span class="tag">结果</span><div class="res-grid">{res_html}</div></div>
</div>
<div class="ft"><span class="arrow">→</span> {significance}</div></div>"""

    return _wrap_card(html)


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
        if not result.get("format"):
            return None

        html = _render_visual(result)

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
