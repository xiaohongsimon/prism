"""Web frontend routes — HTMX-powered feed, feedback, and channel management."""

import json as _json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader

import markdown as _md

from prism.web.ranking import update_preferences
from prism.web.auth import (
    COOKIE_NAME, validate_session, login, create_admin,
    create_invite, register_with_invite,
)
from prism.web.feed_pool import process_external_feed

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
_PUBLIC_PATHS = {"/login", "/register", "/auth/login", "/auth/register", "/static",
                 "/article", "/briefing", "/creator", "/translate", "/showcase",
                 "/decisions", "/feed", "/sw.js"}


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
    # Auto-inject `is_anonymous` whenever the caller passes `request`, so
    # every template can gate interactive controls (save / like / follow)
    # behind `{% if not is_anonymous %}` without each route threading the
    # flag through by hand.
    if "request" in ctx and "is_anonymous" not in ctx:
        ctx["is_anonymous"] = _get_user(ctx["request"]) is None
    tmpl = _jinja_env.get_template(template_name)
    return HTMLResponse(tmpl.render(**ctx))


def _strip_english_sections(md_text: str) -> str:
    """For bilingual course notes, drop the '### English' block in each lesson.

    Each lesson in docs/notes/*-course-zh.md looks like:

        ## L0 · Introduction
        ### English
        <en paragraphs>
        ### 中文
        <zh paragraphs>

    We strip from '### English' up to (but not including) '### 中文', and also
    drop the '### 中文' heading itself so the Chinese body flows directly
    under the lesson's <h2>.
    """
    import re

    # Remove "### English" and everything up to the next "### 中文" heading
    # (lazy match, DOTALL because paragraphs span lines).
    md_text = re.sub(
        r"###\s*English\s*\n.*?###\s*中文\s*\n",
        "",
        md_text,
        flags=re.DOTALL,
    )
    return md_text


def _wrap_course_lessons(html: str) -> tuple[str, list[dict]]:
    """Wrap each <h2>…</h2> + following block into a collapsible <details>.

    Course notes use h1 for the course title and h2 for each lesson. Splitting
    on '<h2' gives us one segment per lesson; we pull the h2's inner HTML as
    the summary and hide the rest behind an open <details> so users can
    collapse lessons they've already read. The markdown `toc` extension has
    already assigned each h2 a slugified id, which we reuse as both the
    details id (for in-page anchor links) and the TOC entry id.

    Returns (body_html, toc_items) where toc_items is a list of
    {"id": slug, "text": h2_inner_html}.
    """
    import re

    toc: list[dict] = []
    parts = html.split("<h2")
    if len(parts) <= 1:
        return html, toc
    out = [parts[0]]
    for p in parts[1:]:
        segment = "<h2" + p
        m = re.match(r'<h2([^>]*)>(.*?)</h2>(.*)', segment, re.DOTALL)
        if not m:
            out.append(segment)
            continue
        attrs = m.group(1)
        h2_inner = m.group(2)
        body = m.group(3).strip()
        # Drop any trailing <hr/> that markdown inserted between lessons.
        body = re.sub(r"<hr\s*/?>\s*$", "", body, flags=re.IGNORECASE).strip()

        id_match = re.search(r'id="([^"]+)"', attrs)
        lesson_id = id_match.group(1) if id_match else ""

        toc.append({"id": lesson_id, "text": h2_inner})

        id_attr = f' id="{lesson_id}"' if lesson_id else ""
        out.append(
            f'<details class="course-lesson" open{id_attr}>'
            f'<summary class="course-lesson-summary">{h2_inner}</summary>'
            f'<div class="course-lesson-body">{body}</div>'
            f'</details>'
        )
    return "".join(out), toc


def _build_creator_list(conn) -> dict:
    """Build channel-grouped creator list for /feed/following.

    Returns a dict with named buckets so the template can render section
    headers and collapsible groups instead of a flat 130-row timeline:

      {
        "youtube":  [...],      # long-form video channels (top section)
        "podcast":  [...],      # xiaoyuzhou podcasts
        "x_today":  [...],      # X creators with items in the last 24h
        "x_week":   [...],      # X creators with items in last 7d (excl. today)
        "x_silent": [...],      # X creators with nothing in 7d
        "other":    [...],      # HN/Reddit/Arxiv/GitHub/etc aggregators
      }
    """
    from prism.web.ranking import FOLLOW_SOURCE_TYPES
    from datetime import datetime, timedelta, timezone
    import yaml as _yaml

    type_icons = {"youtube": "▶", "x": "𝕏", "follow_builders": "𝕏", "github_releases": "📦", "xiaoyuzhou": "🎙", "course": "🎓"}

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
            """SELECT ri.id as item_id, ri.title, ri.body, ri.body_zh, ri.url, ri.created_at,
                      a.id as article_id, a.subtitle as article_subtitle
               FROM raw_items ri LEFT JOIN articles a ON a.raw_item_id = ri.id
               WHERE ri.source_id = ?
               ORDER BY ri.created_at DESC LIMIT 3""",
            (src["id"],),
        ).fetchall()

        if src_type == "youtube":
            avatar = config.get("avatar", "")
        elif src_type == "xiaoyuzhou":
            avatar = config.get("avatar", "")
        elif src_type == "course":
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

    # Bucket by channel + activity. SQLite created_at is "YYYY-MM-DD HH:MM:SS"
    # in UTC; comparing against ISO strings of the same shape works fine.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today_cutoff = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    week_cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    buckets: dict[str, list] = {
        "youtube": [], "podcast": [], "course": [], "other": [],
        "x_today": [], "x_week": [], "x_silent": [],
    }
    for c in all_creators:
        if c["type"] == "youtube":
            buckets["youtube"].append(c)
        elif c["type"] == "xiaoyuzhou":
            buckets["podcast"].append(c)
        elif c["type"] == "course":
            buckets["course"].append(c)
        elif c["type"] in ("x", "follow_builders"):
            latest = c["latest"] or ""
            if latest >= today_cutoff:
                buckets["x_today"].append(c)
            elif latest >= week_cutoff:
                buckets["x_week"].append(c)
            else:
                buckets["x_silent"].append(c)
        else:
            buckets["other"].append(c)

    # Inside each bucket: pref_score desc, then most-recent first.
    for key in buckets:
        buckets[key].sort(
            key=lambda c: (c["pref_score"], c["latest"] or ""),
            reverse=True,
        )
    return buckets


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
    return RedirectResponse(url="/feed/following", status_code=307)


from prism.web.feed import (
    rank_feed,
    record_feed_action,
    get_followed_authors,
    compress_headline,
)
from prism.personalize import (
    FeedCandidate,
    IdentityReRanker,
    ReRanker,
    UserContext,
)


# Personalization seam. The route holds a Protocol-typed reference so a
# future ranker (embedding re-rank, LLM judge, experiment variant) can be
# swapped in here — or injected by tests — without touching the route
# body. Default is the pass-through IdentityReRanker per tech-stack v7 §5.
_RERANKER: ReRanker = IdentityReRanker()


@web_router.get("/feed", response_class=HTMLResponse)
def feed_index(request: Request):
    return _render("feed.html", request=request, next_offset=0)


@web_router.get("/board", response_class=HTMLResponse)
def board_page(request: Request):
    """Owner-only dashboard: 24h updates + pipeline progress + source health."""
    if _get_user(request) is None:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/login", status_code=307)
    from prism.web.board import get_board_data
    conn = _db(request)
    data = get_board_data(conn)
    return _render("board.html", request=request, **data)


@web_router.get("/feed/following", response_class=HTMLResponse)
def feed_following_index(request: Request):
    """Creator-level list sorted by preference score → each row links to
    the creator's profile page. This replaces the earlier signal-flow
    view — users wanted a list of *who* they follow, not a merged feed."""
    conn = _db(request)
    buckets = _build_creator_list(conn)
    return _render("feed_following.html", request=request, buckets=buckets)


@web_router.get("/export/epub")
def export_following_epub(
    request: Request,
    days: int = 7,
    cap: int = 15,
    max_chars: int = 40000,
):
    """Download followed-source content from the last N days as an EPUB.

    Login-gated — anonymous visitors have no 'following' context and the
    owner's full feed is not meant to be publicly downloadable.

    `cap` / `max_chars` guard against reader freezes on huge payloads
    (微信读书 chokes on thousand-chapter books / >100KB chapters).
    """
    if _get_user(request) is None:
        return RedirectResponse(url="/login", status_code=307)
    days = max(1, min(int(days or 7), 90))  # clamp defensively
    cap = max(0, min(int(cap or 0), 200))
    max_chars = max(0, min(int(max_chars or 0), 200000))

    from prism.pipeline.export import build_epub, default_filename

    conn = _db(request)
    data = build_epub(
        conn, days=days, per_source_cap=cap, max_chars=max_chars
    )
    filename = default_filename(days)
    return Response(
        content=data,
        media_type="application/epub+zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@web_router.get("/feed/more", response_class=HTMLResponse)
def feed_more(request: Request, offset: int = 0, limit: int = 10):
    conn = _db(request)
    rows = rank_feed(conn, limit=limit, offset=offset)
    if not rows:
        tpl = _jinja_env.get_template("partials/feed_empty.html")
        return HTMLResponse(tpl.render(view="all"))

    # Personalization seam: wrap rows as FeedCandidate → delegate to the
    # ReRanker Protocol → unwrap payloads. With IdentityReRanker this is
    # a no-op, but the shape is what a real ranker will consume.
    user = _get_user(request)
    is_anon = user is None
    ctx = UserContext(
        user_id=getattr(user, "id", None),
        is_anonymous=is_anon,
        tab="feed",
    )
    candidates = [
        FeedCandidate(
            signal_id=r["signal_id"],
            source_key=(r.get("source_keys") or [None])[0],
            heat=float(r.get("score", 0.0) or 0.0),
            published_at=r.get("created_at"),
            payload=r,
        )
        for r in rows
    ]
    rows = [c.payload for c in _RERANKER.rank(candidates, ctx)]

    followed = get_followed_authors(conn)
    card_tpl = _jinja_env.get_template("partials/feed_card.html")
    # Card template auto-dispatches by source_type (tweet / video / article).
    html = "".join(
        card_tpl.render(signal=r, followed_authors=followed, is_anonymous=is_anon)
        for r in rows
    )
    return HTMLResponse(html)


@web_router.post("/feed/action", response_class=HTMLResponse)
def feed_action(
    request: Request,
    signal_id: int = Form(...),
    action: str = Form(...),
    target_key: str = Form(""),
    response_time_ms: int = Form(0),
):
    if not _get_user(request):
        return HTMLResponse("", status_code=401)
    conn = _db(request)
    interaction_id = record_feed_action(
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


@web_router.post("/feed/click")
def feed_click(request: Request, signal_id: int = Form(...), url: str = Form("")):
    """Click-through beacon: fire-and-forget log for outbound link click.

    Called by the delegated click handler in base.html via
    navigator.sendBeacon, which issues POST with form-encoded body.
    The original <a target=_blank> still navigates — this endpoint just
    returns 204 and updates feed_interactions in the background.

    target_key stores the bare host so analytics can bucket clicks by
    domain without carrying long URLs around.
    """
    from urllib.parse import urlparse
    from fastapi.responses import Response

    # Anonymous clicks are silently discarded — we return 204 so the
    # sendBeacon path still succeeds, but nothing touches feed_interactions.
    if not _get_user(request):
        return Response(status_code=204)

    host = ""
    if url:
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https"):
            host = parsed.netloc

    try:
        conn = _db(request)
        conn.execute(
            "INSERT INTO feed_interactions (signal_id, action, target_key, context_json) "
            "VALUES (?, 'click', ?, ?)",
            (signal_id, host, '{"source":"feed_card"}'),
        )
        conn.commit()
    except Exception:
        pass
    return Response(status_code=204)


@web_router.get("/feed/saved", response_class=HTMLResponse)
def feed_saved(request: Request):
    """Unified saved view across the 3 like/save paths:
       - /feed/action save → feed_interactions
       - /feedback save (article w/ signal) → feedback
       - /article/{id}/like (article w/o signal) → external_feeds
    """
    conn = _db(request)
    # dedup_key collapses dups across the 3 paths:
    # - same signal saved via /feedback AND /feed/action → one row
    # - same article liked via /article/{id}/like multiple times → one row
    rows = conn.execute(
        """SELECT MAX(created_at) AS created_at, summary, topic_label, url, article_id
             FROM (
               SELECT 'sig:' || fi.signal_id AS dedup_key,
                      fi.created_at AS created_at,
                      s.summary AS summary,
                      c.topic_label AS topic_label,
                      (SELECT url FROM raw_items ri
                       JOIN cluster_items ci ON ci.raw_item_id = ri.id
                       WHERE ci.cluster_id = s.cluster_id LIMIT 1) AS url,
                      NULL AS article_id
                 FROM feed_interactions fi
                 JOIN signals s ON s.id = fi.signal_id
                 LEFT JOIN clusters c ON c.id = s.cluster_id
                WHERE fi.action = 'save'

               UNION ALL

               SELECT 'sig:' || fb.signal_id AS dedup_key,
                      fb.created_at AS created_at,
                      s.summary AS summary,
                      c.topic_label AS topic_label,
                      (SELECT url FROM raw_items ri
                       JOIN cluster_items ci ON ci.raw_item_id = ri.id
                       WHERE ci.cluster_id = s.cluster_id LIMIT 1) AS url,
                      NULL AS article_id
                 FROM feedback fb
                 JOIN signals s ON s.id = fb.signal_id
                 LEFT JOIN clusters c ON c.id = s.cluster_id
                WHERE fb.action = 'save'

               UNION ALL

               SELECT 'art:' || a.id AS dedup_key,
                      ef.created_at AS created_at,
                      COALESCE(a.title, ri.title, ef.url) AS summary,
                      COALESCE(a.subtitle, '') AS topic_label,
                      ri.url AS url,
                      a.id AS article_id
                 FROM external_feeds ef
                 JOIN raw_items ri ON ri.url = ef.url
                 JOIN articles a ON a.raw_item_id = ri.id
             )
            GROUP BY dedup_key
            ORDER BY MAX(created_at) DESC
            LIMIT 200"""
    ).fetchall()
    return _render("feed_saved.html", request=request, signals=rows)


@web_router.post("/feedback", response_class=HTMLResponse)
def feedback(
    request: Request,
    signal_id: str = Form(...),
    action: str = Form(...),
):
    """Record feedback and return updated action bar fragment."""
    if not _get_user(request):
        return HTMLResponse("", status_code=401)
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
    if not _get_user(request):
        return HTMLResponse("", status_code=401)
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
    if not _get_user(request):
        return HTMLResponse("", status_code=401)
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


@web_router.post("/sources/add-xyz", response_class=HTMLResponse)
def sources_add_xyz(request: Request, url: str = Form(...)):
    """Accept a xiaoyuzhou.fm podcast URL → append to sources.yaml + sync.

    Returns a small HTML fragment for HTMX to swap into #add-xyz-result.
    """
    import re
    import json as _j
    import urllib.request
    from urllib.request import Request as _Req, urlopen as _urlopen

    if not _get_user(request):
        return HTMLResponse(
            '<div style="color:#c55">请先登录</div>', status_code=401
        )

    # Parse pid from URL: https://www.xiaoyuzhoufm.com/podcast/{24-hex-id}
    m = re.search(r"/podcast/([0-9a-fA-F]{16,32})", url or "")
    if not m:
        return HTMLResponse(
            '<div style="color:#c55">❌ URL 格式不对。期望形如 '
            'https://www.xiaoyuzhoufm.com/podcast/xxxxxxxxxxxxxxxxxxxx</div>'
        )
    pid = m.group(1)

    # Fetch metadata via the xyz _next/data endpoint (same tactic as discover)
    from prism.pipeline.xyz_queue import BUILD_ID, UA
    meta_url = f"https://www.xiaoyuzhoufm.com/_next/data/{BUILD_ID}/podcast/{pid}.json"
    try:
        req = _Req(meta_url, headers={"User-Agent": UA})
        with _urlopen(req, timeout=15) as r:
            data = _j.load(r)
    except Exception as e:
        return HTMLResponse(
            f'<div style="color:#c55">❌ 抓取 pid 元数据失败：{e}</div>'
        )
    page = (data.get("pageProps") or {}).get("podcast") or {}
    if not page:
        return HTMLResponse(
            '<div style="color:#c55">❌ 该 pid 未在小宇宙找到，检查 URL</div>'
        )
    name = (page.get("title") or "").strip() or f"podcast_{pid[:8]}"
    avatar = (page.get("image") or {}).get("picUrl") or ""

    # Check if pid already registered in yaml
    from prism.pipeline.external_feed import _sources_yaml_path
    from prism.sources.yaml_editor import load_sources_list, append_source_block
    yaml_path = _sources_yaml_path()
    existing = load_sources_list(yaml_path)
    for entry in existing:
        if entry.get("type") == "xiaoyuzhou" and entry.get("pid") == pid:
            return HTMLResponse(
                f'<div style="color:#aaa">ℹ 已存在：{entry.get("display_name") or entry.get("key")}</div>'
            )

    # Build YAML entry. Auto-generate key from pid tail (unique).
    new_key = f"xyz:p{pid[-10:]}"
    new_entry = {
        "display_name": name,
        "key": new_key,
        "pid": pid,
        "type": "xiaoyuzhou",
    }
    if avatar:
        new_entry["avatar"] = avatar

    # append_source_block dedupes by _source_key which is too coarse for xyz
    # (all xyz entries collide on 'xiaoyuzhou'). We already checked pid dedup
    # above; skip the helper's check by appending directly.
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 4096
    doc = y.load(yaml_path.read_text(encoding="utf-8"))
    cm = CommentedMap()
    cm.update(new_entry)
    doc["sources"].append(cm)
    with yaml_path.open("w", encoding="utf-8") as f:
        y.dump(doc, f)

    # Sync YAML → DB so the board picks up the new source immediately.
    from prism.source_manager import reconcile_sources
    conn = _db(request)
    reconcile_sources(conn, yaml_path)

    # Also mark the Apple candidate (if name matches) as subscribed so it
    # stops showing in 🏆 候选播客.
    conn.execute(
        "UPDATE xyz_rank_candidate SET subscribed = 1 WHERE name = ?",
        (name,),
    )
    conn.commit()

    # Trigger discover in background so episodes enter the queue without
    # blocking the HTTP response.
    def _bg_discover():
        from prism.pipeline import xyz_queue as q
        from prism.db import get_connection
        from prism.config import settings
        _c = get_connection(settings.db_path)
        try:
            q.discover(_c)
        except Exception as _e:
            pass

    import threading
    threading.Thread(target=_bg_discover, daemon=True).start()

    return HTMLResponse(
        f'<div style="color:#4c9">✅ 已加入：<b>{name}</b> ({new_key})。'
        f'episodes 正在后台 discover，稍后刷新看板可见。</div>'
    )


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
    elif source["type"] == "xiaoyuzhou":
        avatar = config.get("avatar", "")
        pid = config.get("pid", "")
        source_url = f"https://www.xiaoyuzhoufm.com/podcast/{pid}" if pid else ""
    elif source["type"] in ("x", "follow_builders"):
        handle = source["handle"] or source_key.split(":")[-1]
        avatar = f"https://unavatar.io/x/{handle}"
        source_url = f"https://x.com/{handle}"
    elif source["type"] == "course":
        avatar = config.get("avatar", "")
        source_url = config.get("course_url", "")
    else:
        avatar = ""
        source_url = ""

    # Course-specific: render the bilingual / organized notes markdown inline
    # so the creator page actually shows the content, not just a path hint.
    course_notes_html = ""
    course_toc: list[dict] = []
    if source["type"] == "course":
        notes_rel = (config.get("notes_path") or "").strip()
        if notes_rel:
            project_root = Path(__file__).resolve().parents[2]
            notes_abs = (project_root / notes_rel).resolve()
            # Path traversal guard: the resolved file must live under project root.
            try:
                notes_abs.relative_to(project_root)
            except ValueError:
                notes_abs = None  # escape attempt — silently drop
            if notes_abs and notes_abs.is_file():
                try:
                    md_text = notes_abs.read_text(encoding="utf-8")
                    md_text = _strip_english_sections(md_text)
                    course_notes_html = _md.markdown(
                        md_text,
                        extensions=["extra", "toc", "sane_lists"],
                    )
                    course_notes_html, course_toc = _wrap_course_lessons(course_notes_html)
                except Exception:
                    course_notes_html = ""

    # Feed-style signal cards for this creator. Pull the full signal pool
    # (no age cutoff, no pairwise-recent filter, no diversity cap) and
    # keep only signals whose cluster includes a raw_item from this source.
    from prism.web.feed_pool import _get_candidate_pool
    pool = _get_candidate_pool(
        conn,
        apply_diversity_cap=False,
        max_age_days=None,
    )
    signals = [s for s in pool if source_key in (s.get("source_keys") or [])]
    signals.sort(key=lambda s: s.get("published_at") or s.get("created_at") or "", reverse=True)
    signals = signals[:100]

    # Still expose the raw_items tail — creator page also wants to show
    # un-analyzed items (freshly synced, no signal yet) so the user can
    # see what's pending. We render these under the signal cards.
    # Creator page shows ALL raw_items for the source, regardless of whether
    # they've been clustered into a signal. Previously we excluded items with
    # current signals and rendered them as a separate feed-card section, but
    # that produced two inconsistent card styles on the same page. The signal
    # abstraction is irrelevant on a creator page — the user just wants a
    # unified list of this creator's items.
    pending = conn.execute(
        """SELECT ri.id, ri.url, ri.title, ri.body, ri.body_zh, ri.author,
                  ri.created_at, ri.published_at,
                  a.id as article_id, a.subtitle as article_subtitle, a.word_count,
                  (a.highlights_json IS NOT NULL AND a.highlights_json != '') AS articlized,
                  EXISTS(SELECT 1 FROM item_interactions ii
                         WHERE ii.item_id = ri.id AND ii.action = 'like') AS liked
           FROM raw_items ri
           LEFT JOIN articles a ON a.raw_item_id = ri.id
           WHERE ri.source_id = ?
           ORDER BY ri.published_at DESC, ri.created_at DESC
           LIMIT 50""",
        (source["id"],),
    ).fetchall()

    from prism.web.feed import get_followed_authors
    followed = get_followed_authors(conn)

    return _render(
        "creator_profile.html",
        request=request,
        source=source,
        display_name=display_name,
        avatar=avatar,
        source_url=source_url,
        source_type=source["type"],
        signals=signals,
        pending_items=[dict(r) for r in pending],
        followed_authors=followed,
        course_notes_html=course_notes_html,
        course_toc=course_toc,
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
    from prism.pipeline.translate import translate_one
    zh = translate_one(body)
    if zh:
        # Cache only successful translations — empty zh means LLM failure;
        # don't poison the cache so a later batch / retry can fill it.
        conn.execute("UPDATE raw_items SET body_zh = ? WHERE id = ?", (zh, item_id))
        conn.commit()
        display = zh
    else:
        display = body  # fallback to English original
    return HTMLResponse(f'<span class="item-card-subtitle">{display}</span>')


# Lightweight per-item feedback from creator profile pages. Distinct from
# /feed/action because creator-page items are raw_items, not clustered
# signals. A "like" bumps the source_key weight by a small delta so the
# user's accumulated likes on a creator translate into ranking signal.
ITEM_LIKE_SOURCE_DELTA = 0.5


def _like_button_html(item_id: int, liked: bool) -> str:
    if liked:
        return (
            f'<form hx-post="/creator/item/{item_id}/unlike" '
            f'hx-target="this" hx-swap="outerHTML" style="display:inline" '
            f'onclick="event.stopPropagation()">'
            f'<button type="submit" class="item-like liked" title="取消喜欢">❤ 已喜欢</button>'
            f'</form>'
        )
    return (
        f'<form hx-post="/creator/item/{item_id}/like" '
        f'hx-target="this" hx-swap="outerHTML" style="display:inline" '
        f'onclick="event.stopPropagation()">'
        f'<button type="submit" class="item-like" title="喜欢">♡ 喜欢</button>'
        f'</form>'
    )


@web_router.post("/creator/item/{item_id}/like", response_class=HTMLResponse)
def creator_item_like(request: Request, item_id: int):
    if not _get_user(request):
        return HTMLResponse("", status_code=401)
    conn = _db(request)
    row = conn.execute(
        "SELECT s.source_key FROM raw_items ri "
        "JOIN sources s ON ri.source_id = s.id WHERE ri.id = ?",
        (item_id,),
    ).fetchone()
    if not row:
        return HTMLResponse("", status_code=404)
    cur = conn.execute(
        "INSERT OR IGNORE INTO item_interactions (item_id, action) VALUES (?, 'like')",
        (item_id,),
    )
    if cur.rowcount > 0:
        # First-time like for this item — bump source weight.
        conn.execute(
            "INSERT INTO preference_weights (dimension, key, weight, updated_at) "
            "VALUES ('source', ?, ?, strftime('%Y-%m-%dT%H:%M:%S','now')) "
            "ON CONFLICT(dimension, key) DO UPDATE SET "
            "weight = weight + excluded.weight, updated_at = excluded.updated_at",
            (row["source_key"], ITEM_LIKE_SOURCE_DELTA),
        )
    conn.commit()
    return HTMLResponse(_like_button_html(item_id, liked=True))


@web_router.post("/creator/item/{item_id}/unlike", response_class=HTMLResponse)
def creator_item_unlike(request: Request, item_id: int):
    if not _get_user(request):
        return HTMLResponse("", status_code=401)
    conn = _db(request)
    row = conn.execute(
        "SELECT s.source_key FROM raw_items ri "
        "JOIN sources s ON ri.source_id = s.id WHERE ri.id = ?",
        (item_id,),
    ).fetchone()
    if not row:
        return HTMLResponse("", status_code=404)
    cur = conn.execute(
        "DELETE FROM item_interactions WHERE item_id = ? AND action = 'like'",
        (item_id,),
    )
    if cur.rowcount > 0:
        # Reverse the source-weight bump applied at like time.
        conn.execute(
            "UPDATE preference_weights SET weight = weight - ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%S','now') "
            "WHERE dimension = 'source' AND key = ?",
            (ITEM_LIKE_SOURCE_DELTA, row["source_key"]),
        )
    conn.commit()
    return HTMLResponse(_like_button_html(item_id, liked=False))


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

    # Record a 'click' interaction so we can bucket this signal as
    # impressed+clicked (vs impressed-only or saved). Best-effort: a
    # logging failure must never break the article page.
    # Anonymous page views (public /article) don't get logged — otherwise
    # stranger traffic would skew the owner's CTR and preference model.
    if signal_id is not None and _get_user(request):
        try:
            conn.execute(
                "INSERT INTO feed_interactions (signal_id, action, target_key, context_json) "
                "VALUES (?, 'click', ?, ?)",
                (signal_id, f"article:{article_id}",
                 '{"source":"article_detail"}'),
            )
            conn.commit()
        except Exception:
            pass

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
        return HTMLResponse('<div class="feed-card feed-done">已收到 ✓</div>')
    return HTMLResponse('<div class="feed-card feed-done">请输入链接或话题</div>')


@web_router.get("/pairwise/liked", response_class=HTMLResponse)
def pairwise_liked(request: Request, page: int = 1):
    """Render saved signals page.

    URL path still carries the legacy `/pairwise/` prefix (templates link
    to it from multiple places); the data source has moved from
    `pairwise_comparisons.winner` to `feed_interactions.action='save'`
    following Wave 1 pairwise removal (2026-04-23).
    """
    conn = _db(request)
    per_page = 20
    offset = (page - 1) * per_page

    rows = conn.execute(
        """SELECT fi.signal_id, MAX(fi.created_at) AS liked_at
           FROM feed_interactions fi
           WHERE fi.action = 'save'
           GROUP BY fi.signal_id
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

    # Stats — now sourced from feed_interactions (pairwise_comparisons
    # dropped in Wave 1). `total_votes` = total feed events across all
    # actions; `total_liked` = saves specifically.
    total_votes = conn.execute(
        "SELECT COUNT(*) FROM feed_interactions"
    ).fetchone()[0]
    total_liked = conn.execute(
        "SELECT COUNT(*) FROM feed_interactions WHERE action = 'save'"
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


# ── Quality Watchdog Routes ────────────────────────────────────────────────

@web_router.get("/quality", response_class=HTMLResponse)
def quality_page(request: Request):
    """Open anomalies — the one-stop pipeline health dashboard."""
    from prism.quality.rules import list_open
    conn = _db(request)
    anomalies = list_open(conn)
    # Group for the template: critical first, then warn, then info.
    counts = {"critical": 0, "warn": 0, "info": 0}
    for a in anomalies:
        counts[a["severity"]] = counts.get(a["severity"], 0) + 1
    tpl = _jinja_env.get_template("quality.html")
    return HTMLResponse(tpl.render(anomalies=anomalies, counts=counts))


@web_router.post("/quality/ack/{anomaly_id}", response_class=HTMLResponse)
def quality_ack(anomaly_id: int, request: Request):
    """Acknowledge an open anomaly — hides it until it re-fires."""
    from prism.quality.rules import ack
    conn = _db(request)
    ack(conn, anomaly_id)
    return HTMLResponse("", status_code=200)


@web_router.post("/quality/scan", response_class=HTMLResponse)
def quality_scan_now(request: Request):
    """Run scan on demand and redirect back to /quality."""
    from prism.quality import scan
    conn = _db(request)
    scan(conn)
    return RedirectResponse(url="/quality", status_code=303)


# ── /showcase — public landing page for the GitHub README ─────────────────
#
# This is the page strangers hit first when they find Prism. Everything here
# is aggregated numbers + top-of-funnel signals, no personal preference data.
# Goal: prove in 10 seconds that Prism is a real running system, not a README
# promise.

@web_router.get("/showcase", response_class=HTMLResponse)
def showcase(request: Request):
    conn = _db(request)

    def _one(sql, *args):
        row = conn.execute(sql, args).fetchone()
        return row[0] if row else 0

    # ── Aggregate stats (last 7 days) ─────────────────────────────────────
    sources_total = _one("SELECT COUNT(*) FROM sources WHERE enabled = 1")
    sources_by_type = conn.execute(
        "SELECT type, COUNT(*) AS n FROM sources WHERE enabled = 1 "
        "GROUP BY type ORDER BY n DESC"
    ).fetchall()
    raw_7d = _one(
        "SELECT COUNT(*) FROM raw_items WHERE created_at > datetime('now','-7 days')"
    )
    signals_7d = _one(
        "SELECT COUNT(*) FROM signals WHERE is_current = 1 "
        "AND created_at > datetime('now','-7 days')"
    )
    strategic_7d = _one(
        "SELECT COUNT(*) FROM signals WHERE is_current = 1 "
        "AND signal_layer IN ('actionable','strategic') "
        "AND created_at > datetime('now','-7 days')"
    )
    decisions_30d = _one(
        "SELECT COUNT(*) FROM decision_log WHERE timestamp > datetime('now','-30 days')"
    )
    signals_total = _one("SELECT COUNT(*) FROM signals WHERE is_current = 1")

    # First signal timestamp → days of continuous autonomous operation
    first_sig = conn.execute(
        "SELECT created_at FROM signals ORDER BY created_at ASC LIMIT 1"
    ).fetchone()
    if first_sig and first_sig[0]:
        try:
            first_dt = datetime.fromisoformat(first_sig[0].replace("Z", "+00:00"))
            if first_dt.tzinfo is None:
                first_dt = first_dt.replace(tzinfo=timezone.utc)
            days_running = max(1, (datetime.now(timezone.utc) - first_dt).days)
        except Exception:
            days_running = 0
    else:
        days_running = 0

    # ── Top signals last 7d ───────────────────────────────────────────────
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    sig_rows = conn.execute(
        """SELECT s.id AS signal_id, s.cluster_id, s.summary, s.signal_layer,
                  s.signal_strength, s.why_it_matters, s.tl_perspective,
                  s.created_at, c.topic_label, c.item_count, c.date
           FROM signals s
           JOIN clusters c ON s.cluster_id = c.id
           WHERE s.is_current = 1
             AND s.signal_layer IN ('actionable','strategic')
             AND s.signal_strength >= 3
             AND c.date >= ?
           ORDER BY s.signal_strength DESC, s.created_at DESC
           LIMIT 10""",
        (cutoff,),
    ).fetchall()

    # Best outbound URL per cluster (non-aggregator preferred)
    _aggs = ("news.ycombinator.com", "reddit.com")
    cluster_ids = [r["cluster_id"] for r in sig_rows]
    cluster_url = {}
    if cluster_ids:
        ph = ",".join("?" * len(cluster_ids))
        for r in conn.execute(
            f"""SELECT ci.cluster_id, ri.url FROM cluster_items ci
                JOIN raw_items ri ON ri.id = ci.raw_item_id
                WHERE ci.cluster_id IN ({ph}) AND ri.url LIKE 'http%'""",
            cluster_ids,
        ).fetchall():
            cid, url = r["cluster_id"], r["url"]
            is_agg = any(a in url for a in _aggs)
            if cid not in cluster_url or (not is_agg and any(a in cluster_url[cid] for a in _aggs)):
                cluster_url[cid] = url

    top_signals = []
    for r in sig_rows:
        top_signals.append({
            "signal_id": r["signal_id"],
            "topic_label": r["topic_label"],
            "summary": (r["summary"] or "")[:240],
            "signal_layer": r["signal_layer"],
            "signal_strength": r["signal_strength"],
            "why_it_matters": (r["why_it_matters"] or "")[:180],
            "tl_perspective": (r["tl_perspective"] or "")[:180],
            "item_count": r["item_count"],
            "date": r["date"],
            "url": cluster_url.get(r["cluster_id"]),
        })

    # ── Recent autonomous decisions ───────────────────────────────────────
    decisions = conn.execute(
        """SELECT timestamp, layer, action, reason, context_json
           FROM decision_log
           WHERE timestamp > datetime('now','-14 days')
             AND action != 'x_follow_added'
           ORDER BY timestamp DESC LIMIT 12"""
    ).fetchall()

    # ── Cost estimate: local vs cloud API ─────────────────────────────────
    # Assume ~2k tokens in + ~1k tokens out per signal (cluster summary)
    # Claude Sonnet 4.5 pricing: $3/Mtok in, $15/Mtok out (2026-04)
    est_in_tokens = signals_7d * 2000
    est_out_tokens = signals_7d * 1000
    cloud_cost_7d = (est_in_tokens / 1_000_000) * 3 + (est_out_tokens / 1_000_000) * 15
    cloud_cost_yearly = cloud_cost_7d * (365 / 7)

    return _render(
        "showcase.html",
        request=request,
        stats={
            "sources_total": sources_total,
            "sources_by_type": [dict(r) for r in sources_by_type],
            "raw_7d": raw_7d,
            "signals_7d": signals_7d,
            "strategic_7d": strategic_7d,
            "signals_total": signals_total,
            "decisions_30d": decisions_30d,
            "days_running": days_running,
            "kept_ratio": round(strategic_7d * 100 / signals_7d) if signals_7d else 0,
            "cloud_cost_7d": round(cloud_cost_7d, 2),
            "cloud_cost_yearly": round(cloud_cost_yearly, 0),
            "tokens_week_m": round((est_in_tokens + est_out_tokens) / 1_000_000, 1),
        },
        top_signals=top_signals,
        decisions=[dict(d) for d in decisions],
    )


# ── /decisions/weekly — public audit log of autonomous decisions ──────────
#
# What Prism did this week without being asked. Groups by day, by action type.
# This is the transparency story that SaaS recommenders can't tell — anyone
# who lands here can see exactly what the system chose to change.

@web_router.get("/decisions/weekly", response_class=HTMLResponse)
def decisions_weekly(request: Request):
    conn = _db(request)

    # Action-type counts (top-of-page summary)
    counts = conn.execute(
        """SELECT action, COUNT(*) AS n FROM decision_log
           WHERE timestamp > datetime('now','-7 days')
           GROUP BY action ORDER BY n DESC"""
    ).fetchall()

    # Full timeline, newest first (cap at 300 to keep page snappy)
    rows = conn.execute(
        """SELECT timestamp, layer, action, reason, context_json
           FROM decision_log
           WHERE timestamp > datetime('now','-7 days')
           ORDER BY timestamp DESC
           LIMIT 300"""
    ).fetchall()

    # Group by date
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        ts = r["timestamp"] or ""
        day = ts[:10] if ts else "unknown"
        ctx = {}
        if r["context_json"]:
            try:
                ctx = _json.loads(r["context_json"])
            except Exception:
                pass
        by_date.setdefault(day, []).append({
            "time": ts[11:16] if len(ts) > 11 else "",
            "layer": r["layer"],
            "action": r["action"],
            "reason": r["reason"] or "",
            "context": ctx,
        })

    days = sorted(by_date.keys(), reverse=True)

    return _render(
        "decisions_weekly.html",
        request=request,
        counts=[dict(c) for c in counts],
        total=len(rows),
        days=days,
        by_date=by_date,
    )
