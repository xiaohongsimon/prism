"""Web frontend routes — HTMX-powered feed, feedback, and channel management."""

import json as _json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader

import markdown as _md

from prism.web.ranking import compute_feed, update_preferences
from prism.web.auth import (
    COOKIE_NAME, validate_session, login, create_admin,
    create_invite, register_with_invite,
)
from prism.web.pairwise import (
    select_pair, record_vote, process_external_feed, get_pairwise_history,
    _get_candidate_pool,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)


def _linkify_clusters(text: str, cluster_urls: dict, cluster_labels: dict) -> str:
    """Replace (Cluster N) references with HTML links to original sources."""
    import re
    from markupsafe import Markup

    def _replace(m):
        cid = int(m.group(1))
        url = cluster_urls.get(cid)
        if not url:
            return m.group(0)
        label = cluster_labels.get(cid, f"Cluster {cid}")
        # Truncate long labels
        short = label[:30] + "…" if len(label) > 30 else label
        return f'<a href="{url}" target="_blank" rel="noopener" class="br-ref" title="{label}">↗</a>'

    result = re.sub(r'[（(]Cluster\s+(\d+)[)）]', _replace, text)
    return Markup(result)


_jinja_env.filters["linkify_clusters"] = _linkify_clusters

web_router = APIRouter()

# Public paths that don't need auth
_PUBLIC_PATHS = {"/login", "/register", "/auth/login", "/auth/register", "/static", "/article", "/briefing", "/sw.js"}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _db(request: Request) -> sqlite3.Connection:
    return request.state.db


def _get_user(request: Request) -> dict | None:
    """Get authenticated user from session cookie."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return validate_session(_db(request), token)


def _render(template_name: str, **ctx) -> HTMLResponse:
    tmpl = _jinja_env.get_template(template_name)
    return HTMLResponse(tmpl.render(**ctx))


def _build_creator_list(conn) -> dict:
    """Build creator list for follow tab: YouTube channels + timeline of others.

    Returns {"youtube": [creators sorted by latest], "timeline": [creators sorted by latest]}
    YouTube gets a prominent section; X/builders/github merge into a single timeline.
    """
    from prism.web.ranking import FOLLOW_SOURCE_TYPES
    import yaml as _yaml

    type_icons = {"youtube": "▶", "x": "𝕏", "follow_builders": "𝕏", "github_releases": "📦"}

    sources = conn.execute(
        """SELECT s.id, s.source_key, s.type, s.handle, s.config_yaml
           FROM sources s
           WHERE s.type IN ({}) AND s.enabled = 1""".format(
            ",".join("?" * len(FOLLOW_SOURCE_TYPES))
        ),
        list(FOLLOW_SOURCE_TYPES),
    ).fetchall()

    # Load preference scores: source_key → weight, author/handle → weight
    pref_by_source = {}
    pref_by_author = {}
    for row in conn.execute(
        "SELECT dimension, key, weight FROM preference_weights WHERE dimension IN ('source', 'author')"
    ).fetchall():
        if row["dimension"] == "source":
            pref_by_source[row["key"]] = row["weight"]
        else:
            pref_by_author[row["key"]] = row["weight"]

    all_creators = []

    for src in sources:
        src_type = src["type"]
        config = {}
        if src["config_yaml"]:
            try:
                config = _yaml.safe_load(src["config_yaml"]) or {}
            except Exception:
                pass

        display_name = config.get("display_name", src["handle"] or src["source_key"])
        handle = src["handle"] or src["source_key"].split(":")[-1]

        items_info = conn.execute(
            "SELECT count(*) as cnt, max(created_at) as latest FROM raw_items WHERE source_id = ?",
            (src["id"],),
        ).fetchone()

        recent = conn.execute(
            """SELECT ri.id as item_id, ri.title, ri.body, ri.url, ri.created_at,
                      a.id as article_id, a.subtitle as article_subtitle
               FROM raw_items ri LEFT JOIN articles a ON a.raw_item_id = ri.id
               WHERE ri.source_id = ?
               ORDER BY ri.created_at DESC LIMIT 3""",
            (src["id"],),
        ).fetchall()

        if src_type == "youtube":
            avatar = config.get("avatar", "")
        elif src_type in ("x", "follow_builders"):
            avatar = f"https://unavatar.io/x/{handle}"
        else:
            avatar = ""

        # Author pref stored under display_name (e.g. "最佳拍档") or handle (e.g. "karpathy")
        author_score = max(
            pref_by_author.get(handle, 0.0),
            pref_by_author.get(display_name, 0.0),
        )
        pref_score = pref_by_source.get(src["source_key"], 0.0) + author_score

        all_creators.append({
            "source_key": src["source_key"],
            "type": src_type,
            "icon": type_icons.get(src_type, "📌"),
            "display_name": display_name,
            "avatar": avatar,
            "item_count": items_info["cnt"] if items_info else 0,
            "latest": items_info["latest"] if items_info else "",
            "recent_items": [dict(r) for r in recent],
            "pref_score": round(pref_score, 1),
        })

    all_creators.sort(key=lambda c: (c["pref_score"], c["latest"] or ""), reverse=True)
    return all_creators


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

# ── Auth Routes ──────────────────────────────────────────────────────────────

@web_router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return _render("login.html", mode="login", error="")


@web_router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return _render("login.html", mode="register", error="")


@web_router.post("/auth/login")
def auth_login(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = _db(request)
    token = login(conn, username, password)
    if not token:
        return _render("login.html", mode="login", error="用户名或密码错误")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE_NAME, token, max_age=86400 * 30, httponly=True, samesite="lax")
    return resp


@web_router.post("/auth/register")
def auth_register(request: Request, code: str = Form(...), username: str = Form(...), password: str = Form(...)):
    conn = _db(request)
    token = register_with_invite(conn, code, username, password)
    if not token:
        return _render("login.html", mode="register", error="邀请码无效或已使用")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE_NAME, token, max_age=86400 * 30, httponly=True, samesite="lax")
    return resp


@web_router.get("/auth/logout")
def auth_logout(request: Request):
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@web_router.get("/auth/invite", response_class=HTMLResponse)
def gen_invite(request: Request):
    user = _get_user(request)
    if not user or user["role"] != "admin":
        return HTMLResponse("无权限", status_code=403)
    code = create_invite(_db(request), user["user_id"])
    return HTMLResponse(f'<div style="padding:40px;text-align:center;color:#ebebf0;font-size:24px;font-family:monospace">{code}</div>')


# ── Feed Routes ──────────────────────────────────────────────────────────────

@web_router.get("/", response_class=HTMLResponse)
def index(request: Request):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/feed", status_code=307)


from prism.web.feed import rank_feed, record_feed_action


@web_router.get("/feed", response_class=HTMLResponse)
def feed_index(request: Request):
    tpl = _jinja_env.get_template("feed.html")
    return HTMLResponse(tpl.render(next_offset=0))


@web_router.get("/feed/more", response_class=HTMLResponse)
def feed_more(request: Request, offset: int = 0, limit: int = 10):
    conn = _db(request)
    rows = rank_feed(conn, limit=limit, offset=offset)
    if not rows:
        tpl = _jinja_env.get_template("partials/feed_empty.html")
        return HTMLResponse(tpl.render())
    card_tpl = _jinja_env.get_template("partials/feed_card.html")
    html = "".join(card_tpl.render(signal=r) for r in rows)
    return HTMLResponse(html)


@web_router.post("/feed/action", response_class=HTMLResponse)
def feed_action(
    request: Request,
    signal_id: int = Form(...),
    action: str = Form(...),
    target_key: str = Form(""),
    response_time_ms: int = Form(0),
):
    conn = _db(request)
    record_feed_action(
        conn,
        signal_id=signal_id,
        action=action,
        target_key=target_key,
        response_time_ms=response_time_ms,
    )
    if action in ("save", "dismiss"):
        label = "已保存" if action == "save" else "已隐藏"
        return HTMLResponse(
            f'<div class="feed-card feed-done">{label} ✓</div>'
        )
    labels = {
        "follow_author": f"已关注 {target_key}",
        "mute_topic": f"已屏蔽 #{target_key}",
        "unfollow_author": f"取消关注 {target_key}",
        "unmute_topic": f"取消屏蔽 #{target_key}",
    }
    return HTMLResponse(
        f'<span class="btn btn-done">{labels.get(action, "ok")}</span>'
    )


@web_router.get("/feed/saved", response_class=HTMLResponse)
def feed_saved(request: Request):
    conn = _db(request)
    rows = conn.execute(
        """SELECT fi.created_at, s.summary, c.topic_label,
                  (SELECT url FROM raw_items ri
                   JOIN cluster_items ci ON ci.raw_item_id = ri.id
                   WHERE ci.cluster_id = s.cluster_id LIMIT 1) AS url
             FROM feed_interactions fi
             JOIN signals s ON s.id = fi.signal_id
             LEFT JOIN clusters c ON c.id = s.cluster_id
            WHERE fi.action = 'save'
            ORDER BY fi.created_at DESC
            LIMIT 200"""
    ).fetchall()
    tpl = _jinja_env.get_template("feed_saved.html")
    return HTMLResponse(tpl.render(signals=rows))


@web_router.get("/feed/legacy", response_class=HTMLResponse)
def feed_legacy_fragment(
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
            f'<div hx-get="/feed/legacy?tab={tab}&page={page + 1}&per_page={per_page}"'
            f' hx-trigger="revealed"'
            f' hx-target="#feed-list"'
            f' hx-swap="beforeend"'
            f' class="loading">加载中…</div>'
        )

    if not items:
        html_parts.append('<div class="empty">暂无内容</div>')

    return HTMLResponse("".join(html_parts))


@web_router.get("/slides/{signal_id}", response_class=HTMLResponse)
def slides_page(request: Request, signal_id: int):
    """Generate or serve cached HTML slides for a signal."""
    conn = _db(request)
    from prism.web.slides import get_or_generate_slides
    html = get_or_generate_slides(conn, signal_id)
    if not html:
        return HTMLResponse("<div class='empty'>该内容暂不支持生成精华 PPT</div>", status_code=404)
    return HTMLResponse(html)


@web_router.get("/slides/{signal_id}/alt", response_class=HTMLResponse)
def slides_runner_up(request: Request, signal_id: int):
    """Serve the runner-up slides (2nd place from horse race)."""
    conn = _db(request)
    row = conn.execute(
        "SELECT html, model_id FROM signal_slides WHERE signal_id = ?", (-signal_id,)
    ).fetchone()
    if not row:
        return HTMLResponse("<div class='empty'>无备选 PPT</div>", status_code=404)
    return HTMLResponse(row["html"])


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

    # If from article page, return article-style like button
    referer = request.headers.get("hx-current-url", "") or request.headers.get("referer", "")
    if "/article/" in referer:
        liked = action == "save"
        html = (
            '<div class="article-actions">'
            f'<form method="POST" action="/feedback" hx-post="/feedback" hx-swap="outerHTML" hx-target="closest .article-actions">'
            f'<input type="hidden" name="signal_id" value="{sig_id}">'
            f'<input type="hidden" name="action" value="save">'
            f'<button type="submit" class="btn-article-like {"liked" if liked else ""}">'
            f'{"❤️ 已喜欢" if liked else "🤍 喜欢这篇"}'
            '</button></form></div>'
        )
        return HTMLResponse(html)

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
    html = (
        f'<button class="follow-btn"'
        f' hx-post="/channel/{source_key}/follow"'
        f' hx-swap="outerHTML">关注</button>'
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
    html = (
        f'<button class="unfollow-btn"'
        f' hx-post="/channel/{source_key}/unfollow"'
        f' hx-swap="outerHTML">取消关注</button>'
    )
    return HTMLResponse(html)


@web_router.get("/creator/{source_key:path}", response_class=HTMLResponse)
def creator_profile(request: Request, source_key: str):
    """Creator profile — list of videos/tweets for a specific source."""
    conn = _db(request)
    source = conn.execute(
        "SELECT * FROM sources WHERE source_key = ?", (source_key,)
    ).fetchone()
    if not source:
        return HTMLResponse("<div class='empty'>创作者不存在</div>", status_code=404)

    import yaml as _yaml
    config = {}
    if source["config_yaml"]:
        try:
            config = _yaml.safe_load(source["config_yaml"]) or {}
        except Exception:
            pass

    display_name = config.get("display_name", source["handle"] or source_key)
    channel_id = config.get("channel_id", "")

    if source["type"] == "youtube":
        avatar = config.get("avatar", "")
        source_url = f"https://www.youtube.com/channel/{channel_id}" if channel_id else ""
    elif source["type"] in ("x", "follow_builders"):
        handle = source["handle"] or source_key.split(":")[-1]
        avatar = f"https://unavatar.io/x/{handle}"
        source_url = f"https://x.com/{handle}"
    else:
        avatar = ""
        source_url = ""

    items = conn.execute(
        """SELECT ri.id, ri.url, ri.title, ri.body, ri.body_zh, ri.author, ri.created_at, ri.published_at,
                  a.id as article_id, a.subtitle as article_subtitle, a.word_count
           FROM raw_items ri
           LEFT JOIN articles a ON a.raw_item_id = ri.id
           WHERE ri.source_id = ?
           ORDER BY ri.published_at DESC, ri.created_at DESC
           LIMIT 100""",
        (source["id"],),
    ).fetchall()

    return _render(
        "creator_profile.html",
        request=request,
        source=source,
        display_name=display_name,
        avatar=avatar,
        source_url=source_url,
        source_type=source["type"],
        items=[dict(r) for r in items],
    )


@web_router.get("/translate/{item_id}", response_class=HTMLResponse)
def translate_item(request: Request, item_id: int):
    """Translate a raw_item body to Chinese, cache in body_zh column."""
    conn = _db(request)
    row = conn.execute("SELECT body, body_zh FROM raw_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        return HTMLResponse("")
    if row["body_zh"]:
        return HTMLResponse(f'<span class="item-card-subtitle">{row["body_zh"]}</span>')
    body = (row["body"] or "")[:1500]
    if not body:
        return HTMLResponse("")
    from prism.pipeline.llm import call_llm
    try:
        zh = call_llm(
            prompt=f"将以下英文推文翻译为简洁流畅的中文，只输出译文，不要解释：\n\n{body}",
            system="你是翻译助手。忠实翻译，语言简洁自然。",
            max_tokens=512,
        ).strip()
        # strip think tags if present
        import re
        zh = re.sub(r"<think>.*?</think>", "", zh, flags=re.DOTALL).strip()
        conn.execute("UPDATE raw_items SET body_zh = ? WHERE id = ?", (zh, item_id))
        conn.commit()
    except Exception:
        zh = body  # fallback to original
    return HTMLResponse(f'<span class="item-card-subtitle">{zh}</span>')


@web_router.get("/article/{article_id}", response_class=HTMLResponse)
def article_detail(request: Request, article_id: int):
    """Article detail page — structured content from video subtitles."""
    conn = _db(request)
    row = conn.execute(
        """SELECT a.*, ri.url as source_url, ri.author, ri.published_at, ri.created_at as item_created,
                  s.source_key, s.type as source_type
           FROM articles a
           JOIN raw_items ri ON a.raw_item_id = ri.id
           JOIN sources s ON ri.source_id = s.id
           WHERE a.id = ?""",
        (article_id,),
    ).fetchone()

    if not row:
        return HTMLResponse("<div class='empty'>文章不存在</div>", status_code=404)

    body_html = _md.markdown(
        row["structured_body"] or "",
        extensions=["extra", "sane_lists"],
    )

    import json
    highlights = []
    if row["highlights_json"]:
        try:
            highlights = json.loads(row["highlights_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    # Find the signal_id for this article (raw_item → cluster → signal)
    sig_row = conn.execute(
        """SELECT s.id AS signal_id FROM signals s
           JOIN cluster_items ci ON ci.cluster_id = s.cluster_id
           WHERE ci.raw_item_id = ? AND s.is_current = 1
           LIMIT 1""",
        (row["raw_item_id"],),
    ).fetchone()
    signal_id = sig_row["signal_id"] if sig_row else None

    # Check if user already liked this article
    liked = False
    if signal_id:
        fb = conn.execute(
            "SELECT action FROM feedback WHERE signal_id = ? ORDER BY id DESC LIMIT 1",
            (signal_id,),
        ).fetchone()
        liked = fb["action"] == "save" if fb else False
    else:
        # No signal — check external_feeds for this URL as a proxy for "liked"
        ef = conn.execute(
            "SELECT id FROM external_feeds WHERE url = ?", (row["source_url"],)
        ).fetchone()
        liked = ef is not None

    return _render(
        "article.html",
        request=request,
        article=dict(row),
        body_html=body_html,
        highlights=highlights,
        source_key=row["source_key"],
        signal_id=signal_id,
        article_id=article_id,
        source_url=row["source_url"],
        liked=liked,
    )


@web_router.post("/article/{article_id}/like", response_class=HTMLResponse)
def article_like(request: Request, article_id: int):
    """Like an article via external feed mechanism (for articles without signals)."""
    conn = _db(request)
    row = conn.execute(
        "SELECT a.raw_item_id, ri.url FROM articles a JOIN raw_items ri ON a.raw_item_id = ri.id WHERE a.id = ?",
        (article_id,),
    ).fetchone()
    if not row:
        return HTMLResponse("Not found", status_code=404)

    # Use external feed to record as strong positive feedback (weight 3.0)
    process_external_feed(conn, url=row["url"], note="liked from article page")

    html = (
        '<div class="article-actions">'
        f'<form method="POST" action="/article/{article_id}/like" hx-post="/article/{article_id}/like" hx-swap="outerHTML" hx-target="closest .article-actions">'
        '<button type="submit" class="btn-article-like liked">❤️ 已喜欢</button>'
        '</form></div>'
    )
    return HTMLResponse(html)


@web_router.get("/sw.js")
def service_worker():
    """Serve service worker from root path (required for SW scope)."""
    return FileResponse(
        TEMPLATES_DIR.parent / "static" / "sw.js",
        media_type="application/javascript",
    )


# ── Briefing Route ─────────────────────────────────────────────────────────

@web_router.get("/briefing", response_class=HTMLResponse)
def daily_briefing(request: Request):
    """Today's must-know signals — top actionable + strategic items."""
    conn = _db(request)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Get top signals from recent days only (last 2 days to handle timezone gaps)
    cutoff = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT s.id AS signal_id, s.cluster_id, s.summary, s.content_zh, s.signal_layer,
                  s.signal_strength, s.why_it_matters, s.action, s.tl_perspective,
                  s.tags_json, s.created_at, c.topic_label, c.item_count, c.date
           FROM signals s
           JOIN clusters c ON s.cluster_id = c.id
           WHERE s.is_current = 1
             AND s.signal_layer IN ('actionable', 'strategic')
             AND s.signal_strength >= 3
             AND c.date >= ?
           ORDER BY c.date DESC, s.signal_strength DESC, s.created_at DESC
           LIMIT 15""",
        (cutoff,),
    ).fetchall()

    # Fetch best URL for each signal's cluster
    _agg_domains = ("news.ycombinator.com", "reddit.com")
    sig_cluster_ids = [r["cluster_id"] for r in rows]
    item_urls = {}
    if sig_cluster_ids:
        ph2 = ",".join("?" * len(sig_cluster_ids))
        url_rows2 = conn.execute(
            f"""SELECT ci.cluster_id, ri.url
                FROM cluster_items ci
                JOIN raw_items ri ON ri.id = ci.raw_item_id
                WHERE ci.cluster_id IN ({ph2}) AND ri.url LIKE 'http%'
                ORDER BY ci.cluster_id""",
            sig_cluster_ids,
        ).fetchall()
        for ur in url_rows2:
            cid = ur["cluster_id"]
            url = ur["url"]
            is_agg = any(a in url for a in _agg_domains)
            if cid not in item_urls or (not is_agg and any(a in item_urls[cid] for a in _agg_domains)):
                item_urls[cid] = url

    items = []
    for r in rows:
        tags = []
        try:
            tags = _json.loads(r["tags_json"]) if r["tags_json"] else []
        except Exception:
            pass
        items.append({
            "signal_id": r["signal_id"],
            "topic_label": r["topic_label"],
            "summary": r["summary"],
            "content_zh": r["content_zh"] or "",
            "signal_layer": r["signal_layer"],
            "signal_strength": r["signal_strength"],
            "why_it_matters": r["why_it_matters"] or "",
            "action": r["action"] or "",
            "tl_perspective": r["tl_perspective"] or "",
            "tags": tags,
            "date": r["date"],
            "url": item_urls.get(r["cluster_id"], ""),
        })

    # Get daily narrative from recent successful daily analysis (last 2 days only)
    narrative_cutoff = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    narrative_row = conn.execute(
        """SELECT stats_json, started_at FROM job_runs
           WHERE job_type = 'analyze_daily' AND status = 'ok'
             AND date(started_at) >= ?
           ORDER BY id DESC LIMIT 1""",
        (narrative_cutoff,),
    ).fetchone()
    narrative = ""
    if narrative_row:
        try:
            stats = _json.loads(narrative_row["stats_json"])
            narrative = stats.get("briefing_narrative", "")
        except Exception:
            pass

    # Build cluster_id → best URL map for narrative links
    import re
    cluster_ids = [int(m) for m in re.findall(r'Cluster\s+(\d+)', narrative)]
    cluster_urls = {}
    if cluster_ids:
        ph = ",".join("?" * len(cluster_ids))
        url_rows = conn.execute(
            f"""SELECT ci.cluster_id, ri.url, src.type
                FROM cluster_items ci
                JOIN raw_items ri ON ri.id = ci.raw_item_id
                JOIN sources src ON src.id = ri.source_id
                WHERE ci.cluster_id IN ({ph})
                  AND ri.url LIKE 'http%'
                ORDER BY ci.cluster_id""",
            cluster_ids,
        ).fetchall()
        # Pick best URL per cluster: prefer non-aggregator
        _agg = ("news.ycombinator.com", "reddit.com")
        for r in url_rows:
            cid = r["cluster_id"]
            url = r["url"]
            is_agg = any(a in url for a in _agg)
            if cid not in cluster_urls or (not is_agg and any(a in cluster_urls[cid] for a in _agg)):
                cluster_urls[cid] = url

    # Also get topic_label for each cluster (for link title)
    cluster_labels = {}
    if cluster_ids:
        label_rows = conn.execute(
            f"SELECT id, topic_label FROM clusters WHERE id IN ({ph})",
            cluster_ids,
        ).fetchall()
        cluster_labels = {r["id"]: r["topic_label"] for r in label_rows}

    tpl = _jinja_env.get_template("briefing.html")
    return HTMLResponse(tpl.render(
        items=items, narrative=narrative, today=today,
        cluster_urls=cluster_urls, cluster_labels=cluster_labels,
    ))


# ── Notion Export Route ────────────────────────────────────────────────────

@web_router.post("/api/export-notion/{cluster_id}", response_class=JSONResponse)
def export_notion_by_cluster(request: Request, cluster_id: int):
    """Export a cluster's full transcript to Notion."""
    conn = _db(request)
    from prism.config import settings as _cfg

    if not _cfg.notion_api_key or not _cfg.notion_parent_page_id:
        return JSONResponse({"ok": False, "error": "Notion 未配置"}, status_code=400)

    # Get cluster info + longest body
    row = conn.execute(
        """SELECT c.topic_label, ri.body, ri.url, ri.author
           FROM clusters c
           JOIN cluster_items ci ON ci.cluster_id = c.id
           JOIN raw_items ri ON ri.id = ci.raw_item_id
           WHERE c.id = ?
           ORDER BY length(ri.body) DESC LIMIT 1""",
        (cluster_id,),
    ).fetchone()

    if not row or not row["body"] or len(row["body"]) < 50:
        return JSONResponse({"ok": False, "error": "无全文内容"}, status_code=400)

    # Get signal summary/insights for highlights
    sig_row = conn.execute(
        """SELECT s.summary, s.why_it_matters, s.content_zh
           FROM signals s WHERE s.cluster_id = ? AND s.is_current = 1
           ORDER BY s.signal_strength DESC LIMIT 1""",
        (cluster_id,),
    ).fetchone()

    from prism.output.notion import NOTION_API_URL, NOTION_VERSION
    import httpx

    title = row["topic_label"]
    today = datetime.now().strftime("%Y-%m-%d")
    page_title = f"\U0001f4fa [{today}] {title}"

    def _rich(text, bold=False, color="default"):
        return {"type": "text", "text": {"content": text[:2000]},
                "annotations": {"bold": bold, "italic": False, "strikethrough": False,
                                "underline": False, "code": False, "color": color}}

    blocks = []

    # 1. Metadata (bold)
    meta_lines = []
    if row["url"]:
        meta_lines.append(f"视频链接: {row['url']}")
    if row["author"]:
        meta_lines.append(f"频道: {row['author']}")
    meta_lines.append(f"日期: {today}")
    blocks.append({"object": "block", "type": "paragraph",
                   "paragraph": {"rich_text": [_rich("\n".join(meta_lines), bold=True)]}})
    blocks.append({"object": "block", "type": "divider", "divider": {}})

    # 2. AI Summary as callout (if available)
    if sig_row and sig_row["summary"]:
        summary_text = sig_row["content_zh"] or sig_row["summary"]
        blocks.append({"object": "block", "type": "callout",
                       "callout": {"icon": {"type": "emoji", "emoji": "\U0001f4a1"},
                                   "rich_text": [_rich(summary_text[:2000])]}})

    # 3. Why it matters as quote
    if sig_row and sig_row["why_it_matters"]:
        blocks.append({"object": "block", "type": "quote",
                       "quote": {"rich_text": [_rich(sig_row["why_it_matters"][:2000])]}})

    blocks.append({"object": "block", "type": "divider", "divider": {}})

    # 4. Full transcript — split into 2000-char chunks at paragraph boundaries
    blocks.append({"object": "block", "type": "heading_2",
                   "heading_2": {"rich_text": [_rich("全文")]}})
    body = row["body"]
    for para in body.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        while len(para) > 2000:
            blocks.append({"object": "block", "type": "paragraph",
                           "paragraph": {"rich_text": [_rich(para[:2000])]}})
            para = para[2000:]
        blocks.append({"object": "block", "type": "paragraph",
                       "paragraph": {"rich_text": [_rich(para)]}})

    payload = {
        "parent": {"page_id": _cfg.notion_parent_page_id},
        "properties": {
            "title": {"title": [{"text": {"content": page_title[:100]}}]}
        },
        "children": blocks[:100],
    }

    try:
        resp = httpx.post(
            NOTION_API_URL,
            headers={
                "Authorization": f"Bearer {_cfg.notion_api_key}",
                "Content-Type": "application/json",
                "Notion-Version": NOTION_VERSION,
            },
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        notion_url = resp.json().get("url", "")
        return JSONResponse({"ok": True, "url": notion_url})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


# ── Pairwise Routes ────────────────────────────────────────────────────────

@web_router.get("/pairwise/pair", response_class=HTMLResponse)
def pairwise_pair(request: Request):
    """HTMX: return next pair of signals."""
    conn = _db(request)
    pair = select_pair(conn)
    if pair is None:
        tpl = _jinja_env.get_template("partials/pair_empty.html")
        return HTMLResponse(tpl.render())
    a, b, strategy = pair
    tpl = _jinja_env.get_template("partials/pair_cards.html")
    return HTMLResponse(tpl.render(signal_a=a, signal_b=b, strategy=strategy))


@web_router.post("/pairwise/vote", response_class=HTMLResponse)
def pairwise_vote(
    request: Request,
    signal_a_id: int = Form(...),
    signal_b_id: int = Form(...),
    winner: str = Form(...),
    comment: str = Form(""),
    response_time_ms: int = Form(0),
    strategy: str = Form("exploit"),
):
    """Record vote and return next pair immediately.

    - a/b: keep winner at position A, new signal at position B
    - both/neither/skip: load completely new pair
    """
    conn = _db(request)
    record_vote(conn, signal_a_id, signal_b_id, winner, comment, response_time_ms, strategy=strategy)

    tpl = _jinja_env.get_template("partials/pair_cards.html")
    empty_tpl = _jinja_env.get_template("partials/pair_empty.html")

    if winner in ("a", "b"):
        # Keep winner, replace loser with a new signal
        winner_id = signal_a_id if winner == "a" else signal_b_id
        pool = _get_candidate_pool(conn)
        # Find winner in pool
        winner_sig = next((s for s in pool if s["signal_id"] == winner_id), None)
        # Pick a new signal (not the winner, not the loser)
        exclude = {signal_a_id, signal_b_id}
        candidates = [s for s in pool if s["signal_id"] not in exclude]
        if winner_sig and candidates:
            import random
            new_sig = random.choice(candidates)
            return HTMLResponse(tpl.render(signal_a=winner_sig, signal_b=new_sig, strategy="carry_winner"))
        # Fallback: if winner not in pool or no candidates, get fresh pair
        pair = select_pair(conn)
        if pair is None:
            return HTMLResponse(empty_tpl.render())
        a, b, next_strategy = pair
        return HTMLResponse(tpl.render(signal_a=a, signal_b=b, strategy=next_strategy))

    # both / neither / skip → completely new pair
    pair = select_pair(conn)
    if pair is None:
        return HTMLResponse(empty_tpl.render())
    a, b, next_strategy = pair
    return HTMLResponse(tpl.render(signal_a=a, signal_b=b, strategy=next_strategy))


@web_router.post("/pairwise/feed", response_class=HTMLResponse)
def pairwise_feed(
    request: Request,
    url: str = Form(""),
    note: str = Form(""),
):
    """Accept external link/topic as strong positive feedback."""
    conn = _db(request)
    if url.strip():
        process_external_feed(conn, url=url.strip(), note=note.strip())
    pair = select_pair(conn)
    if pair is None:
        tpl = _jinja_env.get_template("partials/pair_empty.html")
        return HTMLResponse(tpl.render(feed_success=True))
    a, b, strategy = pair
    tpl = _jinja_env.get_template("partials/pair_cards.html")
    return HTMLResponse(tpl.render(signal_a=a, signal_b=b, strategy=strategy, feed_success=True))


@web_router.get("/pairwise/liked", response_class=HTMLResponse)
def pairwise_liked(request: Request, page: int = 1):
    """Render liked/saved signals page."""
    conn = _db(request)
    per_page = 20
    offset = (page - 1) * per_page

    # Get signal IDs the user chose as winners, most recent first
    rows = conn.execute(
        """SELECT DISTINCT
                  CASE WHEN pc.winner = 'a' THEN pc.signal_a_id
                       WHEN pc.winner = 'b' THEN pc.signal_b_id END AS signal_id,
                  MAX(pc.created_at) AS liked_at
           FROM pairwise_comparisons pc
           WHERE pc.winner IN ('a', 'b')
           GROUP BY signal_id
           ORDER BY liked_at DESC
           LIMIT ? OFFSET ?""",
        (per_page, offset),
    ).fetchall()

    liked = []
    for r in rows:
        sid = r["signal_id"]
        sig = conn.execute(
            """SELECT s.id AS signal_id, s.cluster_id, s.summary, s.signal_layer,
                      s.signal_strength, s.why_it_matters, s.tags_json, s.created_at,
                      s.content_zh, c.topic_label, c.item_count
               FROM signals s JOIN clusters c ON s.cluster_id = c.id
               WHERE s.id = ?""", (sid,)
        ).fetchone()
        if not sig:
            continue

        import json as _json
        tags = []
        try:
            tags = _json.loads(sig["tags_json"]) if sig["tags_json"] else []
        except Exception:
            pass

        # Get URLs, authors, source_keys, engagement, tweet_text
        detail_rows = conn.execute(
            """SELECT ri.url, ri.author, ri.published_at, ri.raw_json,
                      src.source_key, src.type AS source_type
               FROM cluster_items ci
               JOIN raw_items ri ON ri.id = ci.raw_item_id
               JOIN sources src ON src.id = ri.source_id
               WHERE ci.cluster_id = ?""", (sig["cluster_id"],)
        ).fetchall()

        urls, source_keys, authors = [], [], []
        engagement, tweet_text, published_at = {}, "", None
        _agg = ("news.ycombinator.com", "twitter.com", "x.com", "xcancel.com")
        for dr in detail_rows:
            if dr["url"] and dr["url"].startswith("http") and dr["url"] not in urls:
                if any(d in dr["url"] for d in _agg):
                    urls.append(dr["url"])
                else:
                    urls.insert(0, dr["url"])
            if dr["source_key"] not in source_keys:
                source_keys.append(dr["source_key"])
            if dr["author"] and dr["author"].strip() and dr["author"] not in authors:
                authors.append(dr["author"])
            if dr["published_at"] and (published_at is None or dr["published_at"] < published_at):
                published_at = dr["published_at"]
            if dr["source_type"] == "x":
                try:
                    raw = _json.loads(dr["raw_json"] or "{}")
                    tweet = raw.get("tweet", {})
                    if tweet:
                        engagement = {
                            "likes": tweet.get("favorite_count", 0),
                            "retweets": tweet.get("retweet_count", 0),
                            "replies": tweet.get("reply_count", 0),
                            "quotes": tweet.get("quote_count", 0),
                        }
                        tweet_text = tweet.get("full_text", "") or tweet.get("text", "")
                except Exception:
                    pass

        liked.append({
            "signal_id": sig["signal_id"],
            "topic_label": sig["topic_label"],
            "summary": sig["summary"],
            "why_it_matters": sig["why_it_matters"] or "",
            "signal_strength": sig["signal_strength"],
            "tags": tags,
            "item_count": sig["item_count"],
            "created_at": sig["created_at"],
            "published_at": published_at or sig["created_at"],
            "urls": urls,
            "source_keys": source_keys,
            "authors": authors,
            "engagement": engagement,
            "tweet_text": tweet_text,
            "content_zh": sig["content_zh"] or "",
            "is_video": False,
            "liked_at": r["liked_at"],
        })

    tpl = _jinja_env.get_template("liked.html")
    return HTMLResponse(tpl.render(liked=liked, page=page))


@web_router.get("/pairwise/sources", response_class=HTMLResponse)
def pairwise_sources(request: Request):
    """Render sources overview page."""
    conn = _db(request)
    rows = conn.execute(
        """SELECT s.source_key, s.type, s.handle, s.enabled,
                  COUNT(ri.id) as item_count,
                  MAX(ri.created_at) as last_item
           FROM sources s
           LEFT JOIN raw_items ri ON ri.source_id = s.id
           GROUP BY s.id
           ORDER BY item_count DESC"""
    ).fetchall()

    type_meta = {
        "x": {"label": "X (Twitter)", "icon": "𝕏"},
        "follow_builders": {"label": "X (Twitter)", "icon": "𝕏"},
        "hackernews": {"label": "Hacker News", "icon": "Y"},
        "hn_search": {"label": "HN Search", "icon": "🔍"},
        "reddit": {"label": "Reddit", "icon": "💬"},
        "producthunt": {"label": "Product Hunt", "icon": "🚀"},
        "arxiv": {"label": "arXiv", "icon": "📄"},
        "youtube": {"label": "YouTube", "icon": "▶"},
        "github_trending": {"label": "GitHub Trending", "icon": "⚡"},
        "github_releases": {"label": "GitHub Releases", "icon": "📦"},
        "claude_sessions": {"label": "Claude Sessions", "icon": "◇"},
    }

    # Group sources by type
    from collections import OrderedDict
    groups = OrderedDict()
    for r in rows:
        t = r["type"]
        meta = type_meta.get(t, {"label": t, "icon": "•"})
        group_key = meta["label"]
        if group_key not in groups:
            groups[group_key] = {"icon": meta["icon"], "sources": [], "total": 0}
        handle = r["handle"] or r["source_key"].split(":")[-1]
        # Avatar for X handles
        avatar = ""
        if t in ("x", "follow_builders"):
            avatar = f"https://unavatar.io/x/{handle}"
        elif t in ("github_trending", "github_releases"):
            owner = handle.split("/")[0] if "/" in handle else handle
            avatar = f"https://github.com/{owner}.png?size=40"
        groups[group_key]["sources"].append({
            "key": r["source_key"],
            "handle": handle,
            "avatar": avatar,
            "item_count": r["item_count"],
            "last_item": (r["last_item"] or "")[:10],
            "enabled": r["enabled"],
        })
        groups[group_key]["total"] += r["item_count"]

    total_items = sum(s["item_count"] for s in (dict(r) for r in rows))
    total_sources = len(rows)
    tpl = _jinja_env.get_template("sources.html")
    return HTMLResponse(tpl.render(groups=groups, total_items=total_items, total_sources=total_sources))


@web_router.get("/pairwise/profile", response_class=HTMLResponse)
def pairwise_profile(request: Request):
    """Render preference profile page."""
    conn = _db(request)

    # Top liked tags/topics
    liked_tags = conn.execute(
        "SELECT key, weight FROM preference_weights WHERE dimension = 'tag' AND weight > 0 "
        "ORDER BY weight DESC LIMIT 15"
    ).fetchall()

    # Disliked tags
    disliked_tags = conn.execute(
        "SELECT key, weight FROM preference_weights WHERE dimension = 'tag' AND weight < 0 "
        "ORDER BY weight ASC LIMIT 10"
    ).fetchall()

    # Liked sources
    liked_sources = conn.execute(
        "SELECT key, weight FROM preference_weights WHERE dimension = 'source' AND weight > 0 "
        "ORDER BY weight DESC LIMIT 10"
    ).fetchall()

    # Disliked sources
    disliked_sources = conn.execute(
        "SELECT key, weight FROM preference_weights WHERE dimension = 'source' AND weight < 0 "
        "ORDER BY weight ASC LIMIT 10"
    ).fetchall()

    # Liked authors
    liked_authors = conn.execute(
        "SELECT key, weight FROM preference_weights WHERE dimension = 'author' AND weight > 0 "
        "ORDER BY weight DESC LIMIT 10"
    ).fetchall()

    # Stats
    total_votes = conn.execute("SELECT COUNT(*) FROM pairwise_comparisons").fetchone()[0]
    total_liked = conn.execute(
        "SELECT COUNT(*) FROM pairwise_comparisons WHERE winner IN ('a', 'b')"
    ).fetchone()[0]

    tpl = _jinja_env.get_template("profile.html")
    return HTMLResponse(tpl.render(
        liked_tags=[dict(r) for r in liked_tags],
        disliked_tags=[dict(r) for r in disliked_tags],
        liked_sources=[dict(r) for r in liked_sources],
        disliked_sources=[dict(r) for r in disliked_sources],
        liked_authors=[dict(r) for r in liked_authors],
        total_votes=total_votes,
        total_liked=total_liked,
    ))


@web_router.post("/pairwise/profile/delete")
async def pairwise_profile_delete(request: Request):
    """Delete or reset a single preference weight."""
    body = await request.json()
    dimension = body.get("dimension", "")
    key = body.get("key", "")
    if not dimension or not key:
        return JSONResponse({"ok": False, "error": "missing dimension or key"}, status_code=400)

    conn = _db(request)
    conn.execute(
        "DELETE FROM preference_weights WHERE dimension = ? AND key = ?",
        (dimension, key),
    )
    conn.commit()
    return JSONResponse({"ok": True})


@web_router.post("/pairwise/profile/block")
async def pairwise_profile_block(request: Request):
    """Hard-block a tag or source — items matching it are fully excluded from feed."""
    body = await request.json()
    dimension = body.get("dimension", "")
    key = body.get("key", "")
    if not dimension or not key:
        return JSONResponse({"ok": False, "error": "missing dimension or key"}, status_code=400)

    conn = _db(request)
    conn.execute(
        "INSERT OR REPLACE INTO preference_weights (dimension, key, weight, updated_at) "
        "VALUES (?, ?, -100.0, strftime('%Y-%m-%dT%H:%M:%S', 'now'))",
        (dimension, key),
    )
    conn.commit()
    return JSONResponse({"ok": True})


# --- Persona ---

@web_router.get("/persona", response_class=HTMLResponse)
def persona_form(request: Request):
    return _render("persona.html", request=request)


@web_router.post("/persona")
def persona_submit(
    request: Request,
    role: str = Form(...),
    goals: list[str] = Form(default=[]),
    active_learning: str = Form(""),
    seed_handles: str = Form(""),
    dislike: str = Form(""),
    style: list[str] = Form(default=[]),
    language: str = Form("都行"),
    length: str = Form("都可以"),
    free_text: str = Form(""),
):
    from prism.persona import save_snapshot, extract_from_snapshot

    answers = {
        "role": role,
        "goals": goals,
        "active_learning": active_learning,
        "dislike": dislike,
        "style": style,
        "language": language,
        "length": length,
    }
    handles = [h.strip() for h in seed_handles.splitlines() if h.strip()]

    conn = _db(request)
    snap_id = save_snapshot(
        conn, answers=answers, free_text=free_text, seed_handles=handles,
    )
    extract_from_snapshot(conn, snap_id)

    return RedirectResponse(url="/taste/sources", status_code=303)


# --- Taste / source proposals ---

_ORIGIN_LABELS = {
    "persona": "来自 persona 描述",
    "external_feed": "来自外部投喂",
    "graph_expansion": "来自高权重源的邻居",
    "gap": "来自话题覆盖缺口",
    "blindspot": "盲点扫描发现",
    "manual": "手动添加",
}


def _origin_label(origin: str) -> str:
    return _ORIGIN_LABELS.get(origin, origin)


@web_router.get("/taste/sources", response_class=HTMLResponse)
def taste_sources_list(request: Request):
    import yaml as _yaml
    conn = _db(request)
    rows = conn.execute(
        "SELECT id, source_type, source_config_json, display_name, rationale, origin "
        "FROM source_proposals WHERE status = 'pending' ORDER BY origin, id DESC"
    ).fetchall()

    groups: dict[str, list[dict]] = {}
    for r in rows:
        cfg = _json.loads(r[2])
        groups.setdefault(r[5], []).append({
            "id": r[0], "source_type": r[1],
            "source_config_pretty": _yaml.safe_dump(cfg, allow_unicode=True).strip(),
            "display_name": r[3], "rationale": r[4],
        })

    return _render("taste_sources.html", groups=groups, origin_label=_origin_label)


@web_router.post("/taste/sources/{proposal_id}/accept", response_class=HTMLResponse)
def taste_source_accept(proposal_id: int, request: Request):
    from prism.sources.yaml_editor import append_source_block
    from prism.config import settings

    conn = _db(request)
    row = conn.execute(
        "SELECT source_type, source_config_json, display_name, origin "
        "FROM source_proposals WHERE id = ? AND status = 'pending'",
        (proposal_id,),
    ).fetchone()
    if not row:
        return HTMLResponse("", status_code=404)

    cfg = _json.loads(row[1])
    cfg.setdefault("type", row[0])
    append_source_block(
        Path(settings.source_config),
        source_config=cfg,
        category_comment=f"proposed via {row[3]}",
    )
    conn.execute(
        "UPDATE source_proposals SET status = 'accepted', "
        "reviewed_at = datetime('now') WHERE id = ?",
        (proposal_id,),
    )
    conn.execute(
        "INSERT INTO decision_log (layer, action, reason, context_json) "
        "VALUES ('recall', 'add_source', ?, ?)",
        (f"accepted proposal #{proposal_id}", _json.dumps({"config": cfg, "origin": row[3]})),
    )
    conn.commit()
    return HTMLResponse(f'<li class="muted">已接受：{row[2]}</li>')


@web_router.post("/taste/sources/{proposal_id}/reject", response_class=HTMLResponse)
def taste_source_reject(proposal_id: int, request: Request):
    conn = _db(request)
    row = conn.execute(
        "SELECT display_name FROM source_proposals WHERE id = ? AND status = 'pending'",
        (proposal_id,),
    ).fetchone()
    if not row:
        return HTMLResponse("", status_code=404)
    conn.execute(
        "UPDATE source_proposals SET status = 'rejected', "
        "reviewed_at = datetime('now') WHERE id = ?",
        (proposal_id,),
    )
    conn.execute(
        "INSERT INTO decision_log (layer, action, reason, context_json) "
        "VALUES ('recall', 'reject_source', ?, '{}')",
        (f"rejected proposal #{proposal_id}",),
    )
    conn.commit()
    return HTMLResponse(f'<li class="muted">已拒绝：{row[0]}</li>')
