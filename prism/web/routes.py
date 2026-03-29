"""Web frontend routes — HTMX-powered feed, feedback, and channel management."""

import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader

from prism.web.ranking import compute_feed, update_preferences

TEMPLATES_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)

web_router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _db(request: Request) -> sqlite3.Connection:
    return request.state.db


def _render(template_name: str, **ctx) -> HTMLResponse:
    tmpl = _jinja_env.get_template(template_name)
    return HTMLResponse(tmpl.render(**ctx))


def _feedback_map(conn: sqlite3.Connection, signal_ids: list[int]) -> dict[int, str]:
    """Return {signal_id: latest_action} for the given signal ids."""
    if not signal_ids:
        return {}
    placeholders = ",".join("?" * len(signal_ids))
    rows = conn.execute(
        f"""
        SELECT signal_id, action
        FROM feedback
        WHERE signal_id IN ({placeholders})
        AND id IN (
            SELECT MAX(id) FROM feedback WHERE signal_id IN ({placeholders})
            GROUP BY signal_id
        )
        """,
        signal_ids + signal_ids,
    ).fetchall()
    return {r["signal_id"]: r["action"] for r in rows}


# ── Routes ────────────────────────────────────────────────────────────────────

@web_router.get("/", response_class=HTMLResponse)
def index(request: Request):
    """Full feed page."""
    conn = _db(request)
    tab = "recommend"
    per_page = 20
    items = compute_feed(conn, tab=tab, page=1, per_page=per_page)
    signal_ids = [item["signal_id"] for item in items]
    feedback_map = _feedback_map(conn, signal_ids)
    return _render(
        "feed.html",
        items=items,
        tab=tab,
        page=1,
        per_page=per_page,
        feedback_map=feedback_map,
    )


@web_router.get("/feed", response_class=HTMLResponse)
def feed_fragment(
    request: Request,
    tab: str = "recommend",
    page: int = 1,
    per_page: int = 20,
):
    """HTMX feed fragment — renders cards only (no base layout)."""
    conn = _db(request)
    items = compute_feed(conn, tab=tab, page=page, per_page=per_page)
    signal_ids = [item["signal_id"] for item in items]
    feedback_map = _feedback_map(conn, signal_ids)

    card_tmpl = _jinja_env.get_template("partials/card.html")
    html_parts = []
    for item in items:
        feedback_state = feedback_map.get(item["signal_id"])
        html_parts.append(card_tmpl.render(item=item, feedback_state=feedback_state))

    if len(items) >= per_page:
        html_parts.append(
            f'<div hx-get="/feed?tab={tab}&page={page + 1}&per_page={per_page}"'
            f' hx-trigger="revealed"'
            f' hx-target="#feed-list"'
            f' hx-swap="beforeend"'
            f' class="loading">加载中…</div>'
        )

    if not items:
        html_parts.append('<div class="empty">暂无内容</div>')

    return HTMLResponse("".join(html_parts))


@web_router.post("/feedback", response_class=HTMLResponse)
def feedback(
    request: Request,
    signal_id: str = Form(...),
    action: str = Form(...),
):
    """Record feedback and return updated action bar fragment."""
    conn = _db(request)
    sig_id = int(signal_id)

    conn.execute(
        "INSERT INTO feedback (signal_id, action) VALUES (?, ?)",
        (sig_id, action),
    )
    conn.commit()
    update_preferences(conn, sig_id, action)

    # Build a minimal item dict just for the template
    row = conn.execute(
        "SELECT id AS signal_id FROM signals WHERE id = ?", (sig_id,)
    ).fetchone()
    item = {"signal_id": sig_id}
    feedback_state = action

    tmpl = _jinja_env.get_template("partials/card_actions.html")
    return HTMLResponse(tmpl.render(item=item, feedback_state=feedback_state))


@web_router.get("/channel/{source_key:path}", response_class=HTMLResponse)
def channel_page(request: Request, source_key: str):
    """Full channel page showing signals from a specific source."""
    conn = _db(request)

    source_row = conn.execute(
        "SELECT enabled FROM sources WHERE source_key = ?", (source_key,)
    ).fetchone()
    enabled = bool(source_row["enabled"]) if source_row else False

    # Get signals from this source via cluster_items → raw_items → sources
    rows = conn.execute(
        """
        SELECT DISTINCT s.id AS signal_id, s.cluster_id, s.summary, s.signal_layer,
               s.signal_strength, s.why_it_matters, s.tags_json, s.created_at,
               c.topic_label, c.item_count, c.date AS cluster_date
        FROM signals s
        JOIN clusters c ON s.cluster_id = c.id
        JOIN cluster_items ci ON ci.cluster_id = c.id
        JOIN raw_items ri ON ri.id = ci.raw_item_id
        JOIN sources src ON src.id = ri.source_id
        WHERE src.source_key = ? AND s.is_current = 1
        ORDER BY s.created_at DESC
        LIMIT 50
        """,
        (source_key,),
    ).fetchall()

    import json as _json
    items = []
    for r in rows:
        tags = []
        try:
            tags = _json.loads(r["tags_json"]) if r["tags_json"] else []
        except (ValueError, TypeError):
            pass
        items.append({
            "signal_id": r["signal_id"],
            "cluster_id": r["cluster_id"],
            "topic_label": r["topic_label"],
            "summary": r["summary"],
            "signal_layer": r["signal_layer"],
            "signal_strength": r["signal_strength"],
            "why_it_matters": r["why_it_matters"],
            "item_count": r["item_count"],
            "tags": tags,
            "source_keys": [source_key],
            "cluster_date": r["cluster_date"],
            "created_at": r["created_at"],
        })

    signal_ids = [item["signal_id"] for item in items]
    feedback_map = _feedback_map(conn, signal_ids)

    return _render(
        "channel.html",
        source_key=source_key,
        enabled=enabled,
        item_count=len(items),
        items=items,
        feedback_map=feedback_map,
    )


@web_router.post("/channel/{source_key:path}/unfollow", response_class=HTMLResponse)
def channel_unfollow(request: Request, source_key: str):
    """Unfollow a channel — returns follow button HTML fragment."""
    conn = _db(request)
    conn.execute(
        "UPDATE sources SET enabled = 0 WHERE source_key = ?", (source_key,)
    )
    conn.commit()
    # Return the follow button so user can re-follow
    html = (
        f'<button class="follow-btn"'
        f' hx-post="/channel/{source_key}/follow"'
        f' hx-target="#follow-btn-{source_key}"'
        f' hx-swap="innerHTML">关注</button>'
    )
    return HTMLResponse(html)


@web_router.post("/channel/{source_key:path}/follow", response_class=HTMLResponse)
def channel_follow(request: Request, source_key: str):
    """Follow a channel — returns unfollow button HTML fragment."""
    conn = _db(request)
    conn.execute(
        "UPDATE sources SET enabled = 1 WHERE source_key = ?", (source_key,)
    )
    conn.commit()
    # Return the unfollow button
    html = (
        f'<button class="unfollow-btn"'
        f' hx-post="/channel/{source_key}/unfollow"'
        f' hx-target="#follow-btn-{source_key}"'
        f' hx-swap="innerHTML">取消关注</button>'
    )
    return HTMLResponse(html)
