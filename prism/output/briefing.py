"""Daily briefing generation: HTML + Markdown output."""

import json
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


TEMPLATE_DIR = Path(__file__).parent / "templates"


def _load_signals(conn: sqlite3.Connection, date: str) -> list[dict]:
    """Load signals with cluster topic labels. Prefers daily, falls back to incremental."""
    rows = conn.execute(
        "SELECT s.*, c.topic_label FROM signals s "
        "JOIN clusters c ON s.cluster_id = c.id "
        "WHERE c.date = ? AND s.is_current = 1 AND s.analysis_type = 'daily' "
        "ORDER BY s.signal_strength DESC",
        (date,),
    ).fetchall()

    if not rows:
        rows = conn.execute(
            "SELECT s.*, c.topic_label FROM signals s "
            "JOIN clusters c ON s.cluster_id = c.id "
            "WHERE c.date = ? AND s.is_current = 1 AND s.analysis_type = 'incremental' "
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


def _enrich_signals_with_entities(
    conn: sqlite3.Connection, signals: list[dict], date_str: str
) -> list[dict]:
    """Attach entity_context list to each signal dict.

    For each signal we find entity_events that reference the signal's DB id
    (matched via cluster_id + analysis_type), then pull the entity profile and
    any practice events from the last 14 days.

    Each element of signal["entity_context"]:
        {
          "name": str,
          "status": str,
          "week_count": int,
          "practice_note": str | None,  # non-empty if practice overlap found
        }
    """
    # Build a quick mapping: (cluster_id, summary_prefix) → signal_id
    # We need the actual DB signal ids to query entity_events.
    # Fetch signal ids for today's current signals.
    rows = conn.execute(
        """
        SELECT s.id AS signal_id, s.cluster_id, s.summary
        FROM signals s
        JOIN clusters c ON s.cluster_id = c.id
        WHERE c.date = ? AND s.is_current = 1
        """,
        (date_str,),
    ).fetchall()

    # Map cluster_id → signal_id (take first match per cluster to align with _load_signals)
    cluster_to_signal_id: dict[int, int] = {}
    for r in rows:
        cid = r["cluster_id"]
        if cid not in cluster_to_signal_id:
            cluster_to_signal_id[cid] = r["signal_id"]

    window_14d = (
        date.fromisoformat(date_str) - timedelta(days=14)
    ).isoformat()

    enriched = []
    for sig in signals:
        sig = dict(sig)
        cluster_id = sig.get("cluster_id")
        signal_id = cluster_to_signal_id.get(cluster_id) if cluster_id is not None else None

        entity_context = []
        if signal_id is not None:
            # Find entities linked to this signal
            linked = conn.execute(
                """
                SELECT DISTINCT ep.id, ep.display_name, ep.status, ep.event_count_7d
                FROM entity_events ee
                JOIN entity_profiles ep ON ee.entity_id = ep.id
                WHERE ee.signal_id = ?
                """,
                (signal_id,),
            ).fetchall()

            for ent in linked:
                entity_id = ent["id"]
                display_name = ent["display_name"]
                status = ent["status"] or "emerging"
                week_count = ent["event_count_7d"] or 0

                # Check practice overlap: any practice_* event in last 14 days
                practice_row = conn.execute(
                    """
                    SELECT description, date
                    FROM entity_events
                    WHERE entity_id = ?
                      AND event_type LIKE 'practice_%'
                      AND date >= ?
                    ORDER BY date DESC
                    LIMIT 1
                    """,
                    (entity_id, window_14d),
                ).fetchone()

                practice_note = None
                if practice_row:
                    # Also check there are non-practice events in same window
                    has_external = conn.execute(
                        """
                        SELECT 1 FROM entity_events
                        WHERE entity_id = ?
                          AND event_type NOT LIKE 'practice_%'
                          AND date >= ?
                        LIMIT 1
                        """,
                        (entity_id, window_14d),
                    ).fetchone()
                    if has_external:
                        # Format: "你 MM/DD 在 omlx 测过 <description>"
                        try:
                            practice_date = practice_row["date"][:10]
                            dt = date.fromisoformat(practice_date)
                            date_fmt = f"{dt.month}/{dt.day}"
                        except (ValueError, TypeError):
                            date_fmt = practice_row["date"][:10]
                        desc = practice_row["description"] or display_name
                        practice_note = f"你 {date_fmt} 在 omlx 测过 {desc}"

                entity_context.append(
                    {
                        "name": display_name,
                        "status": status,
                        "week_count": week_count,
                        "practice_note": practice_note,
                    }
                )

        sig["entity_context"] = entity_context
        enriched.append(sig)

    return enriched


def _generate_radar_changes(conn: sqlite3.Connection, date_str: str) -> list[str]:
    """Generate human-readable radar change lines for today.

    Categories:
      - Entities first seen today  →  "🆕 新发现: <name>"
      - Practice overlap entities  →  "↗ 实践交叉: <name> (你测过 + 外部新版)"
      - Entities newly growing     →  "↑ 新进 growing: <name>"
      - Entities newly declining   →  "↓ 趋于沉寂: <name> (14天无新信号)"

    Returns empty list if nothing notable.
    """
    lines: list[str] = []

    try:
        today = date.fromisoformat(date_str)
    except ValueError:
        return lines

    window_14d = (today - timedelta(days=14)).isoformat()
    window_7d = (today - timedelta(days=7)).isoformat()

    # --- 1. Newly discovered entities (first_seen_at == today) ---------------
    new_entities = conn.execute(
        """
        SELECT display_name
        FROM entity_profiles
        WHERE first_seen_at LIKE ?
        ORDER BY display_name
        """,
        (f"{date_str}%",),
    ).fetchall()
    for row in new_entities:
        lines.append(f"🆕 新发现: {row['display_name']}")

    # --- 2. Practice overlap: practice + external events in last 14 days -----
    # Find entities that have both practice_* and non-practice events in window
    practice_ids = conn.execute(
        """
        SELECT DISTINCT entity_id
        FROM entity_events
        WHERE event_type LIKE 'practice_%' AND date >= ?
        """,
        (window_14d,),
    ).fetchall()

    for pid_row in practice_ids:
        eid = pid_row["entity_id"]
        has_external = conn.execute(
            """
            SELECT 1 FROM entity_events
            WHERE entity_id = ? AND event_type NOT LIKE 'practice_%' AND date >= ?
            LIMIT 1
            """,
            (eid, window_14d),
        ).fetchone()
        if has_external:
            name_row = conn.execute(
                "SELECT display_name FROM entity_profiles WHERE id = ?",
                (eid,),
            ).fetchone()
            if name_row:
                lines.append(f"↗ 实践交叉: {name_row['display_name']} (你测过 + 外部新版)")

    # --- 3. Growing entities (status='growing' with recent strong activity) --
    growing = conn.execute(
        """
        SELECT display_name
        FROM entity_profiles
        WHERE status = 'growing' AND last_event_at >= ?
        ORDER BY m7_score DESC
        LIMIT 5
        """,
        (window_7d,),
    ).fetchall()
    for row in growing:
        lines.append(f"↑ 新进 growing: {row['display_name']}")

    # --- 4. Declining / going silent entities --------------------------------
    declining = conn.execute(
        """
        SELECT display_name, last_event_at
        FROM entity_profiles
        WHERE status = 'declining'
        ORDER BY display_name
        LIMIT 5
        """,
    ).fetchall()
    for row in declining:
        lines.append(f"↓ 趋于沉寂: {row['display_name']} (14天无新信号)")

    return lines


def _format_entity_context_line(entity_context: list[dict]) -> str | None:
    """Format entity_context list into a single Markdown annotation line.

    Returns None if entity_context is empty.
    Example output:
      📍 vLLM [growing, 本周第3条] | 🔗 你 3/25 在 omlx 测过 speculative decoding
    """
    if not entity_context:
        return None
    parts = []
    for ec in entity_context:
        name = ec.get("name", "")
        status = ec.get("status", "")
        week_count = ec.get("week_count", 0)
        practice_note = ec.get("practice_note")
        badge = f"📍 {name} [{status}, 本周第{week_count}条]"
        if practice_note:
            badge += f" | 🔗 {practice_note}"
        parts.append(badge)
    return "  " + " · ".join(parts)


def _generate_markdown(signals: list[dict], trends: list[dict],
                       cross_links: list[dict], narrative: str,
                       source_health: list[dict], date: str,
                       radar_changes: list[str] | None = None) -> str:
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
            lines.append(f"  → {s['action']}")
            ec_line = _format_entity_context_line(s.get("entity_context") or [])
            if ec_line:
                lines.append(ec_line)
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
            ec_line = _format_entity_context_line(s.get("entity_context") or [])
            if ec_line:
                lines.append(ec_line)
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

    if radar_changes:
        lines += ["## 📡 Radar 变化", ""]
        for rc in radar_changes:
            lines.append(rc)
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

    # Entity enrichment — optional, safe to fail
    try:
        signals = _enrich_signals_with_entities(conn, signals, date)
        radar_changes = _generate_radar_changes(conn, date)
    except Exception:
        radar_changes = []

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
        radar_changes=radar_changes,
        generated_at=generated_at,
    )

    # Generate Markdown
    markdown = _generate_markdown(signals, trends_data, cross_links, narrative,
                                  source_health, date, radar_changes=radar_changes)

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
