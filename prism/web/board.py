"""Dashboard data: rolling 24h updates, pipeline progress, source health.

Used by the /board route. All windows are rolling-24h to avoid timezone
ambiguity (DB timestamps are UTC; user lives in UTC+8).
"""

import sqlite3
from datetime import datetime


# (emoji, display_label) for each source type. Unknown types fall back to
# (📡, raw type string).
TYPE_META: dict[str, tuple[str, str]] = {
    "x": ("𝕏", "X"),
    "follow_builders": ("𝕏", "X (builders)"),
    "x_home": ("𝕏", "X 推荐流"),
    "youtube": ("📺", "YouTube"),
    "youtube_home": ("📺", "YouTube 推荐"),
    "xiaoyuzhou": ("🎙", "小宇宙"),
    "hackernews": ("🟠", "HN"),
    "hn_search": ("🟠", "HN Search"),
    "producthunt": ("🟣", "Product Hunt"),
    "reddit": ("🟥", "Reddit"),
    "arxiv": ("📄", "arXiv"),
    "github_trending": ("⭐", "GitHub Trending"),
    "github_releases": ("📦", "GitHub Releases"),
    "github_home": ("⭐", "GitHub Home"),
    "model_economics": ("💰", "Model Economics"),
    "claude_sessions": ("🟦", "Claude Sessions"),
    "git_practice": ("🔧", "Git Practice"),
}

# Source types whose body is non-Chinese and benefits from translation.
TRANSLATABLE_TYPES = (
    "x", "follow_builders", "x_home",
    "hackernews", "hn_search", "producthunt", "reddit",
)

# Cross-modal types: audio/video that needs transcription (ASR or subtitle
# extraction) before any text pipeline can touch it. xiaoyuzhou uses Whisper
# via com.prism.xyz-queue; YouTube pulls existing subtitles in sync (failing
# when none exist — those sit in "转写待" state with empty body).
CROSS_MODAL_TYPES = ("xiaoyuzhou", "youtube", "youtube_home")

# Long-form types that warrant article processing (structured_body) and
# further visual structuring (highlights_json + TOC).
ARTICLE_ELIGIBLE_TYPES = ("xiaoyuzhou", "youtube", "youtube_home", "course")


def _meta(src_type: str) -> tuple[str, str]:
    return TYPE_META.get(src_type, ("📡", src_type))


def get_source_type_summary(conn: sqlite3.Connection) -> list[dict]:
    """Per source-type: how many sources active in last 24h, item counts."""
    rows = conn.execute("""
      WITH src AS (
        SELECT s.id, s.type,
          COUNT(CASE WHEN COALESCE(ri.published_at, ri.created_at)
                        >= datetime('now','-24 hours') THEN 1 END) AS items_24h,
          COUNT(CASE WHEN COALESCE(ri.published_at, ri.created_at)
                        >= datetime('now','-48 hours')
                       AND COALESCE(ri.published_at, ri.created_at)
                        <  datetime('now','-24 hours') THEN 1 END) AS items_prev24h
        FROM sources s LEFT JOIN raw_items ri ON ri.source_id = s.id
        WHERE s.enabled = 1
        GROUP BY s.id, s.type
      )
      SELECT type,
             SUM(CASE WHEN items_24h > 0 THEN 1 ELSE 0 END) AS active,
             COUNT(*) AS total,
             SUM(items_24h) AS items_today,
             SUM(items_prev24h) AS items_yesterday
      FROM src
      GROUP BY type
      ORDER BY items_today DESC, total DESC
    """).fetchall()

    out = []
    for r in rows:
        emoji, label = _meta(r["type"])
        td = r["items_today"] or 0
        yd = r["items_yesterday"] or 0
        delta_pct = round(100 * (td - yd) / yd) if yd > 0 else None
        out.append({
            "type": r["type"],
            "emoji": emoji,
            "label": label,
            "active": r["active"] or 0,
            "total": r["total"] or 0,
            "items_today": td,
            "items_yesterday": yd,
            "delta_pct": delta_pct,
        })
    return out


def get_active_sources_today(conn: sqlite3.Connection, limit: int = 40) -> list[dict]:
    """Individual sources that produced items in the last 24h, ranked."""
    rows = conn.execute("""
      SELECT s.source_key, s.type, s.handle,
             COUNT(ri.id) AS items_today,
             MAX(COALESCE(ri.published_at, ri.created_at)) AS last_item_at
      FROM sources s JOIN raw_items ri ON ri.source_id = s.id
      WHERE s.enabled = 1
        AND COALESCE(ri.published_at, ri.created_at) >= datetime('now','-24 hours')
      GROUP BY s.id
      ORDER BY items_today DESC, last_item_at DESC
      LIMIT ?
    """, (limit,)).fetchall()
    out = []
    for r in rows:
        emoji, label = _meta(r["type"])
        out.append({
            "source_key": r["source_key"],
            "type": r["type"],
            "type_label": label,
            "type_emoji": emoji,
            "handle": r["handle"],
            "items_today": r["items_today"],
            "last_item_at": r["last_item_at"],
        })
    return out


def get_articles_today(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    """Articles articlized (highlights_json populated) in the last 24h."""
    rows = conn.execute("""
      SELECT a.id, a.title, a.subtitle, a.word_count, a.updated_at,
             ri.url, ri.author,
             s.source_key, s.type
      FROM articles a
      JOIN raw_items ri ON ri.id = a.raw_item_id
      JOIN sources s ON s.id = ri.source_id
      WHERE a.highlights_json IS NOT NULL AND a.highlights_json != ''
        AND a.updated_at >= datetime('now','-24 hours')
      ORDER BY a.updated_at DESC
      LIMIT ?
    """, (limit,)).fetchall()
    out = []
    for r in rows:
        emoji, label = _meta(r["type"])
        out.append({
            "id": r["id"],
            "title": r["title"],
            "subtitle": r["subtitle"],
            "word_count": r["word_count"] or 0,
            "updated_at": r["updated_at"],
            "url": r["url"],
            "author": r["author"],
            "source_key": r["source_key"],
            "type": r["type"],
            "type_label": label,
            "type_emoji": emoji,
        })
    return out


def get_pipeline_state(conn: sqlite3.Connection) -> dict:
    """6-stage pipeline counts for today (last 24h).

    1. 召回      sync raw_items in
    2. 转写      cross-modal → text (ASR for xiaoyuzhou, subtitles for YouTube)
    3. 翻译      non-Chinese body → Chinese (body_zh)
    4. 生成卡片  cluster+analyze → signal (summary + why_it_matters)
    5. 文章加工  articlize → structured_body (coherent long-form article)
    6. 结构化    articlize → highlights_json + TOC (visual layout on article)

    Not every item passes every stage: translate only applies to
    TRANSLATABLE_TYPES, transcribe only to CROSS_MODAL_TYPES, article
    stages only to ARTICLE_ELIGIBLE_TYPES.
    """
    s7 = "datetime('now','-1 days')"

    def pct(n, d):
        return round(100 * n / d) if d else 0

    # 1. 召回
    total_raw = conn.execute(f"""
      SELECT COUNT(*) FROM raw_items
      WHERE COALESCE(published_at, created_at) >= {s7}
    """).fetchone()[0]

    # 2. 转写 — cross-modal sources; done when body populated (YouTube subtitle
    # pulled in sync) OR article row exists (xiaoyuzhou Whisper landed)
    cm_ph = ",".join("?" * len(CROSS_MODAL_TYPES))
    transcribe_total = conn.execute(f"""
      SELECT COUNT(*) FROM raw_items ri JOIN sources s ON s.id = ri.source_id
      WHERE s.type IN ({cm_ph})
        AND COALESCE(ri.published_at, ri.created_at) >= {s7}
    """, CROSS_MODAL_TYPES).fetchone()[0]
    transcribe_done = conn.execute(f"""
      SELECT COUNT(*) FROM raw_items ri
      JOIN sources s ON s.id = ri.source_id
      LEFT JOIN articles a ON a.raw_item_id = ri.id
      WHERE s.type IN ({cm_ph})
        AND COALESCE(ri.published_at, ri.created_at) >= {s7}
        AND ((ri.body IS NOT NULL AND ri.body != '') OR a.id IS NOT NULL)
    """, CROSS_MODAL_TYPES).fetchone()[0]

    # 3. 翻译
    tr_ph = ",".join("?" * len(TRANSLATABLE_TYPES))
    translate_total = conn.execute(f"""
      SELECT COUNT(*) FROM raw_items ri JOIN sources s ON s.id = ri.source_id
      WHERE s.type IN ({tr_ph})
        AND COALESCE(ri.published_at, ri.created_at) >= {s7}
    """, TRANSLATABLE_TYPES).fetchone()[0]
    translated = conn.execute(f"""
      SELECT COUNT(*) FROM raw_items ri JOIN sources s ON s.id = ri.source_id
      WHERE s.type IN ({tr_ph})
        AND COALESCE(ri.published_at, ri.created_at) >= {s7}
        AND ri.body_zh IS NOT NULL AND ri.body_zh != ''
    """, TRANSLATABLE_TYPES).fetchone()[0]

    # 4. 生成卡片 — raw_item landed in a cluster AND cluster has a current signal
    carded = conn.execute(f"""
      SELECT COUNT(DISTINCT ri.id)
      FROM raw_items ri
      JOIN cluster_items ci ON ci.raw_item_id = ri.id
      JOIN signals sg ON sg.cluster_id = ci.cluster_id AND sg.is_current = 1
      WHERE COALESCE(ri.published_at, ri.created_at) >= {s7}
    """).fetchone()[0]
    signals_today = conn.execute(f"""
      SELECT COUNT(*) FROM signals WHERE created_at >= {s7}
    """).fetchone()[0]

    # 5 & 6. Article stages — only long-form types should reach them.
    ar_ph = ",".join("?" * len(ARTICLE_ELIGIBLE_TYPES))
    article_eligible = conn.execute(f"""
      SELECT COUNT(*) FROM raw_items ri JOIN sources s ON s.id = ri.source_id
      WHERE s.type IN ({ar_ph})
        AND COALESCE(ri.published_at, ri.created_at) >= {s7}
    """, ARTICLE_ELIGIBLE_TYPES).fetchone()[0]
    article_processed = conn.execute(f"""
      SELECT COUNT(*) FROM raw_items ri
      JOIN sources s ON s.id = ri.source_id
      JOIN articles a ON a.raw_item_id = ri.id
      WHERE s.type IN ({ar_ph})
        AND COALESCE(ri.published_at, ri.created_at) >= {s7}
        AND a.structured_body IS NOT NULL AND a.structured_body != ''
    """, ARTICLE_ELIGIBLE_TYPES).fetchone()[0]
    article_structured = conn.execute(f"""
      SELECT COUNT(*) FROM raw_items ri
      JOIN sources s ON s.id = ri.source_id
      JOIN articles a ON a.raw_item_id = ri.id
      WHERE s.type IN ({ar_ph})
        AND COALESCE(ri.published_at, ri.created_at) >= {s7}
        AND a.highlights_json IS NOT NULL AND a.highlights_json != ''
    """, ARTICLE_ELIGIBLE_TYPES).fetchone()[0]

    article_structured_today = conn.execute("""
      SELECT COUNT(*) FROM articles
      WHERE highlights_json IS NOT NULL AND highlights_json != ''
        AND updated_at >= datetime('now','-24 hours')
    """).fetchone()[0]

    return {
        # 1 召回
        "recall_total": total_raw,
        # 2 转写
        "transcribe_total": transcribe_total,
        "transcribe_done": transcribe_done,
        "transcribe_pending": max(transcribe_total - transcribe_done, 0),
        "transcribe_pct": pct(transcribe_done, transcribe_total),
        # 3 翻译
        "translate_total": translate_total,
        "translate_done": translated,
        "translate_pending": max(translate_total - translated, 0),
        "translate_pct": pct(translated, translate_total),
        # 4 生成卡片
        "card_total": total_raw,
        "card_done": carded,
        "card_pending": max(total_raw - carded, 0),
        "card_pct": pct(carded, total_raw),
        "signals_today": signals_today,
        # 5 文章加工
        "article_total": article_eligible,
        "article_processed": article_processed,
        "article_processed_pending": max(article_eligible - article_processed, 0),
        "article_processed_pct": pct(article_processed, article_eligible),
        # 6 结构化
        "article_structured": article_structured,
        "article_structured_pending": max(article_processed - article_structured, 0),
        "article_structured_pct": pct(article_structured, article_eligible),
        "article_structured_today": article_structured_today,
    }


def get_health(conn: sqlite3.Connection, list_limit: int = 20) -> dict:
    """Source health surfaces:
    - stale: enabled but no successful sync in 24h
    - disabled: auto-disabled by failure-tracker, with retry time
    - failing: enabled but accumulated failures
    - backlog: pending counts in each pipeline stage
    """
    stale_rows = conn.execute("""
      SELECT source_key, type, last_synced_at
      FROM sources
      WHERE enabled = 1
        AND (last_synced_at IS NULL OR last_synced_at < datetime('now','-24 hours'))
      ORDER BY (last_synced_at IS NULL) DESC, last_synced_at ASC
      LIMIT ?
    """, (list_limit,)).fetchall()

    disabled_rows = conn.execute("""
      SELECT source_key, type, disabled_reason, consecutive_failures, auto_retry_at
      FROM sources
      WHERE enabled = 0 AND disabled_reason = 'auto'
      ORDER BY consecutive_failures DESC
      LIMIT ?
    """, (list_limit,)).fetchall()

    failing_rows = conn.execute("""
      SELECT source_key, type, consecutive_failures
      FROM sources
      WHERE enabled = 1 AND consecutive_failures > 0
      ORDER BY consecutive_failures DESC
      LIMIT ?
    """, (list_limit,)).fetchall()

    # Backlogs (latest 7 days)
    placeholders = ",".join("?" * len(TRANSLATABLE_TYPES))
    translate_backlog = conn.execute(f"""
      SELECT COUNT(*) FROM raw_items ri JOIN sources s ON s.id = ri.source_id
      WHERE s.type IN ({placeholders})
        AND COALESCE(ri.published_at, ri.created_at) >= datetime('now','-7 days')
        AND (ri.body_zh IS NULL OR ri.body_zh = '')
    """, TRANSLATABLE_TYPES).fetchone()[0]

    articlize_backlog = conn.execute("""
      SELECT COUNT(*) FROM articles
      WHERE structured_body IS NOT NULL
        AND (highlights_json IS NULL OR highlights_json = '')
    """).fetchone()[0]

    # Xiaoyuzhou items with no article row → still need transcription
    transcribe_backlog = conn.execute("""
      SELECT COUNT(*) FROM raw_items ri
      JOIN sources s ON s.id = ri.source_id
      LEFT JOIN articles a ON a.raw_item_id = ri.id
      WHERE s.type = 'xiaoyuzhou' AND a.id IS NULL
        AND COALESCE(ri.published_at, ri.created_at) >= datetime('now','-30 days')
    """).fetchone()[0]

    def _row(r, extra: dict | None = None):
        emoji, label = _meta(r["type"])
        d = {"source_key": r["source_key"], "type": r["type"],
             "type_label": label, "type_emoji": emoji}
        d.update(dict(r))
        if extra:
            d.update(extra)
        return d

    return {
        "stale": [_row(r) for r in stale_rows],
        "disabled": [_row(r) for r in disabled_rows],
        "failing": [_row(r) for r in failing_rows],
        "backlog": {
            "translate": translate_backlog,
            "articlize": articlize_backlog,
            "transcribe": transcribe_backlog,
        },
    }


def get_xyz_progress(conn: sqlite3.Connection) -> dict:
    """Per-podcast backfill progress for the xiaoyuzhou transcription queue.

    Each configured `xiaoyuzhou` source is one row. LEFT JOIN so that a
    podcast added to sources.yaml but not yet `discover`'d still shows up
    (with all zeros + "等待 discover" hint).
    """
    rows = conn.execute("""
      SELECT
        s.source_key,
        s.handle AS display_name,
        s.enabled,
        SUM(CASE WHEN q.status='pending'     THEN 1 ELSE 0 END) AS pending,
        SUM(CASE WHEN q.status='transcribed' THEN 1 ELSE 0 END) AS transcribed,
        SUM(CASE WHEN q.status='inserted'    THEN 1 ELSE 0 END) AS inserted,
        SUM(CASE WHEN q.status='done'        THEN 1 ELSE 0 END) AS done,
        COUNT(q.eid)                                             AS total,
        MAX(CASE WHEN q.status='done' THEN q.done_at END)        AS last_done_at,
        SUM(CASE WHEN q.status != 'done' AND q.attempts > 0 THEN 1 ELSE 0 END)
                                                                 AS in_flight,
        SUM(CASE WHEN q.status != 'done' AND q.error IS NOT NULL AND q.error != ''
                 THEN 1 ELSE 0 END)                              AS errored,
        MIN(CASE WHEN q.status='pending' THEN q.pub_date END)    AS oldest_pending_at
      FROM sources s
      LEFT JOIN xyz_episode_queue q ON q.source_key = s.source_key
      WHERE s.type = 'xiaoyuzhou' AND s.enabled = 1
      GROUP BY s.source_key, s.handle, s.enabled
      ORDER BY done DESC, total DESC, s.source_key ASC
    """).fetchall()

    def pct(n, d):
        return round(100 * n / d) if d else 0

    podcasts = []
    tot_pending = tot_trans = tot_ins = tot_done = tot_inflight = tot_err = 0
    for r in rows:
        pending = r["pending"] or 0
        transcribed = r["transcribed"] or 0
        inserted = r["inserted"] or 0
        done = r["done"] or 0
        total = r["total"] or 0
        tot_pending += pending
        tot_trans += transcribed
        tot_ins += inserted
        tot_done += done
        tot_inflight += r["in_flight"] or 0
        tot_err += r["errored"] or 0
        remaining = pending + transcribed + inserted
        podcasts.append({
            "source_key": r["source_key"],
            "display_name": r["display_name"] or r["source_key"],
            "enabled": bool(r["enabled"]),
            "pending": pending,
            "transcribed": transcribed,
            "inserted": inserted,
            "done": done,
            "total": total,
            "remaining": remaining,
            "done_pct": pct(done, total),
            "last_done_at": r["last_done_at"],
            "in_flight": r["in_flight"] or 0,
            "errored": r["errored"] or 0,
            "oldest_pending_at": r["oldest_pending_at"],
            "never_discovered": total == 0,
        })

    # Recent throughput: episodes completed in last 1h / 24h
    done_1h = conn.execute(
        "SELECT COUNT(*) FROM xyz_episode_queue "
        "WHERE status='done' AND done_at >= datetime('now','-1 hours')"
    ).fetchone()[0]
    done_24h = conn.execute(
        "SELECT COUNT(*) FROM xyz_episode_queue "
        "WHERE status='done' AND done_at >= datetime('now','-24 hours')"
    ).fetchone()[0]

    return {
        "podcasts": podcasts,
        "totals": {
            "pending": tot_pending,
            "transcribed": tot_trans,
            "inserted": tot_ins,
            "done": tot_done,
            "in_flight": tot_inflight,
            "errored": tot_err,
            "remaining": tot_pending + tot_trans + tot_ins,
            "done_1h": done_1h,
            "done_24h": done_24h,
        },
    }


def get_xyz_candidates(conn: sqlite3.Connection, limit: int = 20) -> dict:
    """Candidate head podcasts from Apple CN top chart, not yet subscribed.

    Row returned for board rendering; user manually decides to subscribe by
    pasting the xiaoyuzhou.fm URL (→ /sources/add-xyz).
    """
    rows = conn.execute("""
      SELECT apple_id, name, artist, rank, artwork_url, last_seen_at
      FROM xyz_rank_candidate
      WHERE subscribed = 0
      ORDER BY rank ASC
      LIMIT ?
    """, (limit,)).fetchall()

    total = conn.execute(
        "SELECT COUNT(*) FROM xyz_rank_candidate"
    ).fetchone()[0]
    subscribed_cnt = conn.execute(
        "SELECT COUNT(*) FROM xyz_rank_candidate WHERE subscribed = 1"
    ).fetchone()[0]
    last_refresh = conn.execute(
        "SELECT MAX(last_seen_at) FROM xyz_rank_candidate"
    ).fetchone()[0]

    from urllib.parse import quote
    candidates = []
    for r in rows:
        candidates.append({
            "apple_id": r["apple_id"],
            "name": r["name"],
            "artist": r["artist"],
            "rank": r["rank"],
            "artwork_url": r["artwork_url"],
            # Xiaoyuzhou has no web search, but Apple Podcasts listing is at
            # `/cn/podcast/{slug}/id{apple_id}`. Best we can offer is a
            # search on Google site:xiaoyuzhoufm.com for the podcast name.
            "xyz_search_url": f"https://www.google.com/search?q={quote(r['name'] + ' site:xiaoyuzhoufm.com')}",
        })
    return {
        "candidates": candidates,
        "total": total,
        "subscribed_cnt": subscribed_cnt,
        "remaining": total - subscribed_cnt,
        "last_refresh": last_refresh,
    }


def get_youtube_progress(conn: sqlite3.Connection) -> dict:
    """Per-channel backfill progress for the YouTube pipeline.

    Unlike xiaoyuzhou (which has its own queue table), YouTube rides directly
    on raw_items + articles. Stages:
      - no_subtitle: raw_item has no body text → can't articlize
      - awaiting_articlize: body present but no articles row
      - articlize_in_progress: articles row exists, highlights_json empty
      - done: articles with highlights_json populated
    """
    rows = conn.execute("""
      SELECT
        s.source_key,
        s.handle AS handle,
        s.enabled,
        s.config_yaml,
        COUNT(ri.id) AS total,
        SUM(CASE WHEN (ri.body IS NULL OR ri.body = '') THEN 1 ELSE 0 END)
                                                                 AS no_subtitle,
        SUM(CASE WHEN ri.body != '' AND a.id IS NULL THEN 1 ELSE 0 END)
                                                                 AS awaiting_articlize,
        SUM(CASE WHEN a.id IS NOT NULL
                  AND (a.highlights_json IS NULL OR a.highlights_json = '')
                 THEN 1 ELSE 0 END)                              AS articlize_in_progress,
        SUM(CASE WHEN a.highlights_json IS NOT NULL AND a.highlights_json != ''
                 THEN 1 ELSE 0 END)                              AS done,
        MAX(ri.created_at)                                        AS last_fetched_at,
        MAX(CASE WHEN a.highlights_json IS NOT NULL AND a.highlights_json != ''
                 THEN a.updated_at END)                           AS last_done_at
      FROM sources s
      LEFT JOIN raw_items ri ON ri.source_id = s.id
      LEFT JOIN articles a ON a.raw_item_id = ri.id
      WHERE s.type = 'youtube' AND s.enabled = 1
      GROUP BY s.source_key, s.handle, s.enabled, s.config_yaml
      ORDER BY done DESC, total DESC, s.source_key ASC
    """).fetchall()

    def pct(n, d):
        return round(100 * n / d) if d else 0

    def _display_name(handle: str | None, source_key: str, config_yaml: str | None) -> str:
        if config_yaml:
            try:
                import yaml
                cfg = yaml.safe_load(config_yaml) or {}
                if isinstance(cfg, dict) and cfg.get("display_name"):
                    return str(cfg["display_name"])
            except Exception:
                pass
        return handle or source_key.split(":")[-1]

    channels = []
    tot_total = tot_no_sub = tot_wait = tot_progress = tot_done = 0
    for r in rows:
        total = r["total"] or 0
        no_sub = r["no_subtitle"] or 0
        wait = r["awaiting_articlize"] or 0
        progress = r["articlize_in_progress"] or 0
        done = r["done"] or 0
        tot_total += total
        tot_no_sub += no_sub
        tot_wait += wait
        tot_progress += progress
        tot_done += done
        channels.append({
            "source_key": r["source_key"],
            "display_name": _display_name(r["handle"], r["source_key"], r["config_yaml"]),
            "enabled": bool(r["enabled"]),
            "total": total,
            "no_subtitle": no_sub,
            "awaiting_articlize": wait,
            "articlize_in_progress": progress,
            "done": done,
            "remaining": wait + progress,
            "done_pct": pct(done, total),
            "last_fetched_at": r["last_fetched_at"],
            "last_done_at": r["last_done_at"],
        })

    done_24h = conn.execute("""
        SELECT COUNT(*) FROM articles a
        JOIN raw_items ri ON ri.id = a.raw_item_id
        JOIN sources s ON s.id = ri.source_id
        WHERE s.type = 'youtube'
          AND a.highlights_json IS NOT NULL AND a.highlights_json != ''
          AND a.updated_at >= datetime('now','-24 hours')
    """).fetchone()[0]

    return {
        "channels": channels,
        "totals": {
            "total": tot_total,
            "no_subtitle": tot_no_sub,
            "awaiting_articlize": tot_wait,
            "articlize_in_progress": tot_progress,
            "done": tot_done,
            "remaining": tot_wait + tot_progress,
            "done_24h": done_24h,
        },
    }


def get_board_data(conn: sqlite3.Connection) -> dict:
    """Bundle every section's data for the /board template."""
    return {
        "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source_types": get_source_type_summary(conn),
        "active_sources": get_active_sources_today(conn),
        "articles_today": get_articles_today(conn),
        "pipeline": get_pipeline_state(conn),
        "health": get_health(conn),
        "xyz_progress": get_xyz_progress(conn),
        "xyz_candidates": get_xyz_candidates(conn),
        "youtube_progress": get_youtube_progress(conn),
    }
