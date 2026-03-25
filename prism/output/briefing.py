"""Daily briefing generation: HTML + Markdown output."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


TEMPLATE_DIR = Path(__file__).parent / "templates"


def _load_signals(conn: sqlite3.Connection, date: str) -> list[dict]:
    """Load daily signals with cluster topic labels."""
    rows = conn.execute(
        "SELECT s.*, c.topic_label FROM signals s "
        "JOIN clusters c ON s.cluster_id = c.id "
        "WHERE c.date = ? AND s.is_current = 1 AND s.analysis_type = 'daily' "
        "ORDER BY s.signal_strength DESC",
        (date,),
    ).fetchall()

    signals = []
    for r in rows:
        tags = []
        try:
            tags = json.loads(r["tags_json"]) if r["tags_json"] else []
        except (json.JSONDecodeError, TypeError):
            pass
        signals.append({
            "cluster_id": r["cluster_id"],
            "topic_label": r["topic_label"],
            "summary": r["summary"],
            "signal_layer": r["signal_layer"],
            "signal_strength": r["signal_strength"],
            "why_it_matters": r["why_it_matters"],
            "action": r["action"],
            "tl_perspective": r["tl_perspective"],
            "tags": tags,
        })
    return signals


def _load_trends(conn: sqlite3.Connection, date: str) -> list[dict]:
    """Load trends for the date."""
    rows = conn.execute(
        "SELECT * FROM trends WHERE date = ? AND is_current = 1 ORDER BY heat_score DESC",
        (date,),
    ).fetchall()
    return [{"topic_label": r["topic_label"], "heat_score": r["heat_score"],
             "delta": r["delta_vs_yesterday"]} for r in rows]


def _load_cross_links(conn: sqlite3.Connection, date: str) -> list[dict]:
    """Load cross-links for clusters on the date."""
    rows = conn.execute(
        "SELECT cl.*, ca.topic_label as topic_a, cb.topic_label as topic_b "
        "FROM cross_links cl "
        "JOIN clusters ca ON cl.cluster_a_id = ca.id "
        "JOIN clusters cb ON cl.cluster_b_id = cb.id "
        "WHERE ca.date = ? AND cl.is_current = 1",
        (date,),
    ).fetchall()
    return [{"topic_a": r["topic_a"], "topic_b": r["topic_b"],
             "relation_type": r["relation_type"], "reason": r["reason"]} for r in rows]


def _load_narrative(conn: sqlite3.Connection, date: str) -> str:
    """Load briefing narrative from daily analysis job_run."""
    row = conn.execute(
        "SELECT stats_json FROM job_runs WHERE job_type = 'analyze_daily' "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if row:
        try:
            stats = json.loads(row["stats_json"])
            return stats.get("briefing_narrative", "")
        except (json.JSONDecodeError, TypeError):
            pass
    return ""


def _load_source_health(conn: sqlite3.Connection) -> list[dict]:
    """Load disabled/degraded sources."""
    rows = conn.execute(
        "SELECT source_key, enabled, disabled_reason, consecutive_failures "
        "FROM sources WHERE enabled = 0 OR consecutive_failures > 0 "
        "ORDER BY source_key"
    ).fetchall()
    health = []
    for r in rows:
        if not r["enabled"]:
            status = "DISABLED"
        elif r["consecutive_failures"] > 0:
            status = f"DEGRADED ({r['consecutive_failures']} failures)"
        else:
            continue
        health.append({
            "source_key": r["source_key"],
            "status": status,
            "reason": r["disabled_reason"] or "",
        })
    return health


def _generate_markdown(signals: list[dict], trends: list[dict],
                       cross_links: list[dict], narrative: str,
                       source_health: list[dict], date: str) -> str:
    """Generate Markdown version of the briefing."""
    lines = [f"# Prism Daily Brief — {date}", ""]

    if narrative:
        lines += ["## 今日全局", "", narrative, ""]

    actionable = [s for s in signals if s["signal_layer"] == "actionable"]
    if actionable:
        lines += ["## 需要行动", ""]
        for s in actionable:
            lines.append(f"### {s['topic_label']}: {s['summary']}")
            lines.append(f"- **层级**: ACTIONABLE (strength={s['signal_strength']})")
            lines.append(f"- **为什么重要**: {s['why_it_matters']}")
            lines.append(f"- **建议行动**: {s['action']}")
            lines.append(f"- **TL 视角**: {s['tl_perspective']}")
            if s["tags"]:
                lines.append(f"- **标签**: {', '.join(s['tags'])}")
            lines.append("")

    strategic = [s for s in signals if s["signal_layer"] == "strategic"]
    if strategic:
        lines += ["## 值得关注", ""]
        for s in strategic:
            lines.append(f"### {s['topic_label']}: {s['summary']}")
            lines.append(f"- **层级**: STRATEGIC (strength={s['signal_strength']})")
            lines.append(f"- **为什么重要**: {s['why_it_matters']}")
            lines.append(f"- **TL 视角**: {s['tl_perspective']}")
            lines.append("")

    if trends:
        lines += ["## 趋势热力", ""]
        for t in trends:
            delta_str = f"+{t['delta']:.0f}" if t["delta"] > 0 else f"{t['delta']:.0f}"
            lines.append(f"- **{t['topic_label']}**: heat={t['heat_score']:.0f} delta={delta_str}")
        lines.append("")

    if cross_links:
        lines += ["## 关联发现", ""]
        for cl in cross_links:
            lines.append(f"- {cl['topic_a']} ↔ {cl['topic_b']}: {cl['reason']} ({cl['relation_type']})")
        lines.append("")

    if source_health:
        lines += ["## 源健康", ""]
        for sh in source_health:
            lines.append(f"- **{sh['source_key']}**: {sh['status']} {sh['reason']}")
        lines.append("")

    return "\n".join(lines)


def generate_briefing(conn: sqlite3.Connection, date: str, save: bool = False) -> dict:
    """Generate daily briefing in HTML and Markdown.

    Returns dict with 'html' and 'markdown' keys.
    """
    signals = _load_signals(conn, date)
    trends_data = _load_trends(conn, date)
    cross_links = _load_cross_links(conn, date)
    narrative = _load_narrative(conn, date)
    source_health = _load_source_health(conn)

    actionable = [s for s in signals if s["signal_layer"] == "actionable"]
    strategic = [s for s in signals if s["signal_layer"] == "strategic"]

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Render HTML
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("briefing.html.j2")
    html = template.render(
        date=date,
        narrative=narrative,
        actionable_signals=actionable,
        strategic_signals=strategic,
        trends=trends_data,
        cross_links=cross_links,
        source_health=source_health,
        generated_at=generated_at,
    )

    # Generate Markdown
    markdown = _generate_markdown(signals, trends_data, cross_links, narrative,
                                  source_health, date)

    if save:
        # Save to DB
        conn.execute(
            "INSERT OR REPLACE INTO briefings (date, html, markdown) VALUES (?, ?, ?)",
            (date, html, markdown),
        )
        conn.commit()

        # Save to file
        briefings_dir = Path("briefings")
        briefings_dir.mkdir(exist_ok=True)
        (briefings_dir / f"{date}.html").write_text(html)

    return {"html": html, "markdown": markdown}
