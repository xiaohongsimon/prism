"""Prism CLI — Click-based command interface."""

import os
from pathlib import Path

import click
from prism.config import settings
from prism.db import get_connection


@click.group()
def cli():
    """Prism — AI Signal Intelligence System"""
    pass


@cli.command()
@click.option("--source", default=None, help="Sync specific source_key only")
def sync(source):
    """Sync all enabled sources."""
    import asyncio
    from prism.pipeline.sync import run_sync
    conn = get_connection(settings.db_path)
    from prism.source_manager import reconcile_sources
    reconcile_sources(conn, settings.source_config)
    stats = asyncio.run(run_sync(conn, source_key=source))
    click.echo(f"Sync complete: {stats['sources_ok']} ok, {stats['sources_failed']} failed, {stats['items_total']} items")


@cli.command()
def articlize():
    """Generate structured articles from YouTube video subtitles."""
    from prism.pipeline.articlize import run_articlize
    conn = get_connection(settings.db_path)
    stats = run_articlize(conn)
    click.echo(
        f"Articlize complete: {stats['success']} generated, "
        f"{stats['failed']} failed, {stats['skipped']} skipped "
        f"(of {stats['total']} eligible)"
    )


@cli.group()
def source():
    """Manage signal sources."""
    pass


@source.command("list")
def source_list():
    """List all sources and their status."""
    from prism.source_manager import list_sources, reconcile_sources
    conn = get_connection(settings.db_path)
    reconcile_sources(conn, settings.source_config)
    for s in list_sources(conn):
        status = "enabled" if s["enabled"] else f"DISABLED ({s['disabled_reason']})"
        click.echo(f"  {s['source_key']:25s}  {status:20s}  last_sync={s['last_synced_at'] or 'never'}")


@source.command("add")
@click.argument("type")
@click.option("--handle", required=True)
@click.option("--depth", default="tweet")
def source_add(type, handle, depth):
    """Add a new source."""
    from prism.source_manager import add_source
    conn = get_connection(settings.db_path)
    add_source(conn, settings.source_config, type=type, handle=handle, config={"depth": depth})
    click.echo(f"Added {type}:{handle}")


@source.command("remove")
@click.argument("source_key")
def source_remove(source_key):
    """Remove a source."""
    from prism.source_manager import remove_source
    conn = get_connection(settings.db_path)
    remove_source(conn, settings.source_config, source_key)
    click.echo(f"Removed {source_key}")


@source.command("enable")
@click.argument("source_key")
def source_enable(source_key):
    """Re-enable a disabled source."""
    from prism.source_manager import enable_source
    conn = get_connection(settings.db_path)
    enable_source(conn, source_key)
    click.echo(f"Enabled {source_key}")


@cli.command()
@click.option("--eval", "show_eval", is_flag=True, help="Show clustering statistics")
def cluster(show_eval):
    """Run incremental clustering on today's unprocessed items."""
    from datetime import date
    from prism.pipeline.cluster import cluster_items, build_merged_context, cluster_eval_stats
    from prism.pipeline.entities import load_entities, tag_entities
    from prism.source_manager import reconcile_sources

    conn = get_connection(settings.db_path)
    reconcile_sources(conn, settings.source_config)

    today = date.today().isoformat()

    # Load entities if config exists
    entities = None
    if settings.entity_config.exists():
        entities = load_entities(settings.entity_config)

    # Get today's unprocessed items (not yet in any cluster)
    rows = conn.execute(
        "SELECT ri.* FROM raw_items ri "
        "LEFT JOIN cluster_items ci ON ri.id = ci.raw_item_id "
        "WHERE ci.cluster_id IS NULL AND date(ri.created_at) = ?",
        (today,),
    ).fetchall()

    if not rows:
        click.echo("No new items to cluster.")
        return

    from prism.models import RawItem
    items = [
        RawItem(
            id=r["id"], source_id=r["source_id"], url=r["url"],
            title=r["title"], body=r["body"], author=r["author"],
            published_at=r["published_at"], raw_json=r["raw_json"],
        )
        for r in rows
    ]

    # Tag entities
    if entities:
        for item in items:
            tags = tag_entities(item.title, item.body, entities)
            if tags:
                click.echo(f"  Tagged {item.url}: {', '.join(tags)}")

    # Cluster
    clusters = cluster_items(items, existing_clusters=[], entities=entities)

    # Store clusters in DB
    items_by_id = {item.id: item for item in items}
    for c in clusters:
        c_items = [items_by_id[i] for i in c["item_ids"] if i in items_by_id]
        merged = build_merged_context(c_items)
        cursor = conn.execute(
            "INSERT INTO clusters (date, topic_label, item_count, merged_context) VALUES (?, ?, ?, ?)",
            (today, c.get("topic_label", ""), len(c["item_ids"]), merged),
        )
        cluster_id = cursor.lastrowid
        for item_id in c["item_ids"]:
            conn.execute("INSERT OR IGNORE INTO cluster_items (cluster_id, raw_item_id) VALUES (?, ?)",
                         (cluster_id, item_id))
    conn.commit()

    click.echo(f"Clustered {len(items)} items into {len(clusters)} clusters.")

    if show_eval:
        stats = cluster_eval_stats(clusters)
        click.echo(f"  Clusters: {stats['cluster_count']}, Avg size: {stats['avg_size']:.1f}, "
                    f"Max size: {stats['max_size']}, Singleton ratio: {stats['singleton_ratio']:.0%}")


@cli.command()
@click.option("--incremental", is_flag=True, help="Run incremental analysis on new clusters")
@click.option("--daily", is_flag=True, help="Run daily batch analysis")
@click.option("--date", default=None, help="Date for daily analysis (YYYY-MM-DD)")
@click.option("--workers", default=4, help="Max concurrent LLM calls")
def analyze(incremental, daily, date, workers):
    """Run LLM signal analysis."""
    from prism.pipeline.analyze import run_incremental_analysis, run_daily_analysis
    conn = get_connection(settings.db_path)

    if not incremental and not daily:
        click.echo("Specify --incremental or --daily")
        return

    if incremental:
        count = run_incremental_analysis(conn, model=settings.llm_cheap_model, max_workers=workers)
        click.echo(f"Incremental analysis: {count} signals created")

    if daily:
        from datetime import date as date_cls, timedelta
        # Default to yesterday: daily.sh runs early morning before today's clusters exist
        analysis_date = date or (date_cls.today() - timedelta(days=1)).isoformat()
        stats = run_daily_analysis(conn, dt=analysis_date, model=settings.llm_model)
        click.echo(f"Daily analysis: {stats.get('signals_created', 0)} signals, "
                    f"{stats.get('cross_links', 0)} cross-links")


@cli.command()
@click.option("--date", default=None, help="Date to calculate trends for (YYYY-MM-DD)")
def trends(date):
    """Calculate trend heat scores and day-over-day deltas."""
    from datetime import date as date_cls
    from prism.pipeline.trends import calculate_trends
    conn = get_connection(settings.db_path)
    trend_date = date or date_cls.today().isoformat()
    count = calculate_trends(conn, date=trend_date)
    click.echo(f"Trends: {count} topics calculated for {trend_date}")

    # Show top trends
    rows = conn.execute(
        "SELECT topic_label, heat_score, delta_vs_yesterday FROM trends "
        "WHERE date = ? AND is_current = 1 ORDER BY heat_score DESC LIMIT 10",
        (trend_date,),
    ).fetchall()
    for r in rows:
        delta = f"+{r['delta_vs_yesterday']:.0f}" if r["delta_vs_yesterday"] > 0 else f"{r['delta_vs_yesterday']:.0f}"
        click.echo(f"  {r['topic_label']:25s}  heat={r['heat_score']:.0f}  delta={delta}")


@cli.command()
@click.option("--date", default=None, help="Date for briefing (YYYY-MM-DD)")
@click.option("--save", is_flag=True, help="Save to DB and file")
def briefing(date, save):
    """Generate daily briefing."""
    from datetime import date as date_cls
    from prism.output.briefing import generate_briefing
    conn = get_connection(settings.db_path)
    brief_date = date or date_cls.today().isoformat()
    result = generate_briefing(conn, date=brief_date, save=save)
    if save:
        click.echo(f"Briefing saved for {brief_date}")
    else:
        click.echo(result["markdown"])


@cli.command()
@click.option("--port", default=8000, help="Port to listen on")
def serve(port):
    """Start the API server."""
    import uvicorn
    uvicorn.run("prism.api.app:create_app", host="0.0.0.0", port=port, factory=True)


@cli.command("enrich-youtube")
@click.option("--limit", default=10, help="Max videos to process")
def enrich_youtube(limit):
    """Backfill YouTube items with subtitle transcripts."""
    from prism.config import settings
    from prism.db import get_connection
    from prism.sources.subtitles import extract_subtitles

    conn = get_connection(settings.db_path)
    rows = conn.execute(
        """
        SELECT ri.id, ri.url, ri.title, LENGTH(ri.body) as body_len
        FROM raw_items ri
        JOIN sources s ON s.id = ri.source_id
        WHERE s.type = 'youtube'
          AND ri.url NOT LIKE '%/shorts/%'
          AND LENGTH(ri.body) < 500
        ORDER BY ri.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    click.echo(f"Found {len(rows)} YouTube items with short body")
    enriched = 0
    for row in rows:
        click.echo(f"  Processing: {row['title'][:60]}...")
        transcript = extract_subtitles(row["url"])
        if transcript and len(transcript) > row["body_len"]:
            conn.execute(
                "UPDATE raw_items SET body = ? WHERE id = ?",
                (transcript[:8000], row["id"]),
            )
            conn.commit()
            enriched += 1
            click.echo(f"    ✓ {len(transcript)} chars")
        else:
            click.echo("    ✗ no subtitles")

    click.echo(f"\nEnriched {enriched}/{len(rows)} items")


@cli.command("expand-links")
@click.option("--limit", default=20, help="Max items to process")
def expand_links(limit):
    """Resolve t.co short links and fetch YouTube transcripts from tweets."""
    from prism.config import settings
    from prism.db import get_connection
    from prism.sources.link_expander import batch_enrich_links

    conn = get_connection(settings.db_path)
    enriched = batch_enrich_links(conn, limit=limit)
    click.echo(f"Enriched {enriched} items with expanded links")


@cli.command("generate-slides")
@click.option("--limit", default=50, help="Max signals to process")
@click.option("--race", is_flag=True, help="Use 3-model horse race (slower, higher quality)")
def generate_slides(limit, race):
    """Batch generate PPT slides for all eligible signals."""
    from prism.config import settings
    from prism.db import get_connection

    conn = get_connection(settings.db_path)
    # Find signals with enough content but no slides yet
    rows = conn.execute(
        """
        SELECT signal_id, topic_label, body_len FROM (
            SELECT s.id as signal_id, c.topic_label, LENGTH(ri.body) as body_len, src.type,
                   ROW_NUMBER() OVER (PARTITION BY src.type ORDER BY s.signal_strength DESC) as rn
            FROM signals s
            JOIN clusters c ON c.id = s.cluster_id
            JOIN cluster_items ci ON ci.cluster_id = c.id
            JOIN raw_items ri ON ri.id = ci.raw_item_id
            JOIN sources src ON src.id = ri.source_id
            WHERE s.is_current = 1
              AND LENGTH(ri.body) > 80
              AND s.id NOT IN (SELECT signal_id FROM signal_slides WHERE signal_id > 0)
        )
        ORDER BY rn, body_len DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    click.echo(f"Found {len(rows)} signals without slides")
    success = 0
    for i, row in enumerate(rows, 1):
        click.echo(f"  [{i}/{len(rows)}] {row['topic_label'][:60]}...")
        try:
            if race:
                from prism.web.slides import get_or_generate_slides
                html = get_or_generate_slides(conn, row["signal_id"])
            else:
                from prism.web.slides import generate_slides_fast
                html = generate_slides_fast(conn, row["signal_id"])
            if html:
                success += 1
                click.echo(f"    ✓ {len(html)} bytes")
            else:
                click.echo("    ✗ generation failed")
        except Exception as exc:
            click.echo(f"    ✗ {exc}")

    click.echo(f"\nGenerated {success}/{len(rows)} slides")


@cli.command()
def status():
    """Show system status: sources, items, signals."""
    from datetime import date
    conn = get_connection(settings.db_path)
    from prism.source_manager import reconcile_sources
    reconcile_sources(conn, settings.source_config)
    today = date.today().isoformat()

    click.echo("=== Sources ===")
    sources = conn.execute("SELECT * FROM sources ORDER BY source_key").fetchall()
    for s in sources:
        st = "enabled" if s["enabled"] else f"DISABLED ({s['disabled_reason']})"
        fails = f"  failures={s['consecutive_failures']}" if s["consecutive_failures"] > 0 else ""
        click.echo(f"  {s['source_key']:25s}  {st:20s}  last_sync={s['last_synced_at'] or 'never'}{fails}")

    click.echo(f"\n=== Today ({today}) ===")
    item_count = conn.execute("SELECT COUNT(*) FROM raw_items WHERE date(created_at) = ?", (today,)).fetchone()[0]
    cluster_count = conn.execute("SELECT COUNT(*) FROM clusters WHERE date = ?", (today,)).fetchone()[0]
    click.echo(f"  Items: {item_count}  Clusters: {cluster_count}")

    layers = conn.execute(
        "SELECT signal_layer, COUNT(*) as cnt FROM signals s "
        "JOIN clusters c ON s.cluster_id = c.id "
        "WHERE c.date = ? AND s.is_current = 1 GROUP BY signal_layer",
        (today,),
    ).fetchall()
    if layers:
        parts = [f"{r['signal_layer']}={r['cnt']}" for r in layers]
        click.echo(f"  Signals: {', '.join(parts)}")


@cli.command()
@click.option("--notion", is_flag=True, help="Publish to Notion")
@click.option("--date", default=None, help="Date to publish")
def publish(notion, date):
    """Publish briefing to external services."""
    from datetime import date as date_cls
    pub_date = date or date_cls.today().isoformat()
    conn = get_connection(settings.db_path)

    if notion:
        if not settings.notion_api_key or not settings.notion_parent_page_id:
            click.echo("Error: NOTION_API_KEY and NOTION_BRIEFING_PARENT_PAGE_ID must be set")
            return
        row = conn.execute("SELECT markdown FROM briefings WHERE date = ?", (pub_date,)).fetchone()
        if not row:
            click.echo(f"No briefing found for {pub_date}. Run 'prism briefing --save' first.")
            return
        from prism.output.notion import publish_briefing_to_notion
        result = publish_briefing_to_notion(
            markdown=row["markdown"], date=pub_date,
            api_key=settings.notion_api_key, parent_page_id=settings.notion_parent_page_id)
        click.echo(f"Published to Notion: {result.get('id', 'ok')}")
    else:
        click.echo("Specify --notion")


@cli.command("publish-videos")
@click.option("--limit", default=10, help="Max videos to publish")
def publish_videos(limit):
    """Auto-publish YouTube video transcripts to Notion."""
    from datetime import datetime, timedelta
    import httpx

    if not settings.notion_api_key or not settings.notion_parent_page_id:
        click.echo("Error: Notion not configured")
        return

    conn = get_connection(settings.db_path)
    cutoff = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")

    # Ensure notion_exports table exists
    conn.execute("""CREATE TABLE IF NOT EXISTS notion_exports (
        cluster_id INTEGER PRIMARY KEY,
        notion_url TEXT,
        exported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
    )""")
    conn.commit()

    # Find YouTube videos with transcript that haven't been published to Notion
    rows = conn.execute(
        """SELECT c.id AS cluster_id, c.topic_label, ri.body, ri.url, ri.author,
                  ri.published_at, s.summary, s.content_zh, s.why_it_matters
           FROM clusters c
           JOIN cluster_items ci ON ci.cluster_id = c.id
           JOIN raw_items ri ON ri.id = ci.raw_item_id
           JOIN sources src ON src.id = ri.source_id
           LEFT JOIN signals s ON s.cluster_id = c.id AND s.is_current = 1
           WHERE src.type = 'youtube'
             AND ri.url NOT LIKE '%/shorts/%'
             AND length(ri.body) >= 500
             AND c.date >= ?
             AND c.id NOT IN (SELECT cluster_id FROM notion_exports)
           GROUP BY c.id
           HAVING ri.id = (SELECT ci2.raw_item_id FROM cluster_items ci2
                           JOIN raw_items ri2 ON ri2.id = ci2.raw_item_id
                           WHERE ci2.cluster_id = c.id
                           ORDER BY length(ri2.body) DESC LIMIT 1)
           ORDER BY ri.published_at DESC
           LIMIT ?""",
        (cutoff, limit),
    ).fetchall()

    if not rows:
        click.echo("No new videos to publish")
        return

    from prism.output.notion import NOTION_API_URL, NOTION_VERSION
    from prism.pipeline.llm import call_llm
    import re as _re

    STRUCTURE_PROMPT = (
        "你是文本结构化助手。将用户提供的字幕文本分成5-10个章节，每个章节加一个简洁的中文标题。\n"
        "格式要求：\n"
        "1. 用 ## 标题 作为章节分隔\n"
        "2. 每个章节内，将内容整理成通顺的自然段落\n"
        "3. 保留原文含义，不要翻译，但可以修正明显的语音识别错误\n"
        "4. 删除口头禅、重复、无意义的填充词\n"
        "5. 找出直击本质、深刻、反直觉的观点或金句（每篇3-5处），用 <<insight>>这段文字<</insight>> 标记\n"
        "6. 只返回结构化后的文本，不要解释"
    )

    def _structure_transcript(body: str) -> str:
        """Use LLM to add chapter headings + insight markers to raw transcript."""
        try:
            # Always use gemma-4-31b for structuring — 26b has repetition issues
            result = call_llm(body[:6000], system=STRUCTURE_PROMPT,
                              model="gemma-4-31b-it-8bit",
                              max_tokens=4096, timeout=600)
            # Strip thinking tags
            result = _re.sub(r"<think>.*?</think>", "", result, flags=_re.DOTALL).strip()
            if "##" in result and len(result) > 200:
                return result
        except Exception as exc:
            click.echo(f"    LLM structuring failed: {exc}")
        return body  # fallback to raw

    def _markdown_to_notion_blocks(text: str) -> list[dict]:
        """Convert structured markdown to Notion blocks with insight highlighting."""
        blocks = []
        para_buf = []

        def flush():
            if not para_buf:
                return
            content = "\n".join(para_buf).strip()
            if content:
                blocks.append({"object": "block", "type": "paragraph",
                               "paragraph": {"rich_text": _parse_rich(content)}})
            para_buf.clear()

        for line in text.split("\n"):
            stripped = line.strip()
            if stripped == "---":
                flush()
                blocks.append({"object": "block", "type": "divider", "divider": {}})
            elif stripped.startswith("## "):
                flush()
                blocks.append({"object": "block", "type": "heading_2",
                               "heading_2": {"rich_text": [{"type": "text", "text": {"content": stripped[3:][:2000]}}]}})
            elif stripped.startswith("# "):
                flush()
                blocks.append({"object": "block", "type": "heading_1",
                               "heading_1": {"rich_text": [{"type": "text", "text": {"content": stripped[2:][:2000]}}]}})
            elif stripped.startswith("- "):
                flush()
                blocks.append({"object": "block", "type": "bulleted_list_item",
                               "bulleted_list_item": {"rich_text": _parse_rich(stripped[2:])}})
            elif not stripped:
                flush()
            else:
                para_buf.append(line)
        flush()
        return blocks

    def _parse_rich(text: str) -> list[dict]:
        """Parse <<insight>> markers and **bold** into Notion rich_text."""
        parts = _re.split(r"<<insight>>(.*?)<</insight>>", text, flags=_re.DOTALL)
        rich = []
        for i, part in enumerate(parts):
            if not part:
                continue
            if i % 2 == 1:  # insight
                for chunk in _split_2k(part):
                    rich.append({"type": "text", "text": {"content": chunk},
                                 "annotations": {"bold": True, "color": "red"}})
            else:
                bold_parts = _re.split(r"\*\*(.+?)\*\*", part)
                for j, bp in enumerate(bold_parts):
                    if not bp:
                        continue
                    for chunk in _split_2k(bp):
                        item = {"type": "text", "text": {"content": chunk}}
                        if j % 2 == 1:
                            item["annotations"] = {"bold": True}
                        rich.append(item)
        return rich or [{"type": "text", "text": {"content": " "}}]

    def _split_2k(text: str) -> list[str]:
        if len(text) <= 2000:
            return [text]
        chunks = []
        while text:
            if len(text) <= 2000:
                chunks.append(text)
                break
            sp = text.rfind("\n", 0, 2000)
            if sp == -1:
                sp = text.rfind(" ", 0, 2000)
            if sp == -1:
                sp = 2000
            chunks.append(text[:sp])
            text = text[sp:].lstrip()
        return chunks

    published = 0
    for r in rows:
        title = r["topic_label"]
        today = datetime.now().strftime("%Y-%m-%d")
        pub_date = r['published_at'][:10] if r['published_at'] else today
        page_title = f"\U0001f4fa [{pub_date}] {title}"

        click.echo(f"  Structuring: {title[:50]}...")
        structured_body = _structure_transcript(r["body"] or "")

        # Build markdown for Notion
        md_parts = []
        meta = f"**视频链接:** {r['url'] or ''}\n**频道:** {r['author'] or ''}\n**日期:** {pub_date}"
        md_parts.append(meta)
        md_parts.append("---")

        # AI Summary
        summary = r["content_zh"] or r["summary"] or ""
        if summary:
            md_parts.append(f"**💡 核心摘要：** {summary}")
        if r["why_it_matters"]:
            md_parts.append(f"**为什么重要：** {r['why_it_matters']}")
        md_parts.append("---")
        md_parts.append(structured_body)

        full_md = "\n\n".join(md_parts)
        blocks = _markdown_to_notion_blocks(full_md)

        payload = {
            "parent": {"page_id": settings.notion_parent_page_id},
            "properties": {"title": {"title": [{"text": {"content": page_title[:100]}}]}},
            "icon": {"emoji": "\U0001f4fa"},
            "children": blocks[:100],
        }

        try:
            resp = httpx.post(NOTION_API_URL,
                              headers={"Authorization": f"Bearer {settings.notion_api_key}",
                                       "Content-Type": "application/json",
                                       "Notion-Version": NOTION_VERSION},
                              json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            page_id = data.get("id", "")
            notion_url = data.get("url", "")

            # Append remaining blocks if > 100
            remaining = blocks[100:]
            while remaining:
                batch = remaining[:100]
                remaining = remaining[100:]
                httpx.patch(f"https://api.notion.com/v1/blocks/{page_id}/children",
                            headers={"Authorization": f"Bearer {settings.notion_api_key}",
                                     "Content-Type": "application/json",
                                     "Notion-Version": NOTION_VERSION},
                            json={"children": batch}, timeout=30)

            conn.execute("INSERT OR REPLACE INTO notion_exports (cluster_id, notion_url) VALUES (?, ?)",
                         (r["cluster_id"], notion_url))
            conn.commit()
            published += 1
            click.echo(f"  Published: {title[:50]} -> {notion_url}")
        except Exception as exc:
            click.echo(f"  Failed: {title[:50]}: {exc}")

    click.echo(f"Published {published}/{len(rows)} videos to Notion")


@cli.command()
@click.option("--days", default=90, help="Retention period in days")
def cleanup(days):
    """Clean up old data per retention policy."""
    conn = get_connection(settings.db_path)
    # Delete old raw_items (FTS5 triggers handle item_search cleanup)
    cursor = conn.execute(
        f"DELETE FROM raw_items WHERE created_at < datetime('now', '-{int(days)} days')")
    items_deleted = cursor.rowcount
    # Clean old job_runs
    cursor = conn.execute(
        f"DELETE FROM job_runs WHERE started_at < datetime('now', '-{int(days)} days')")
    jobs_deleted = cursor.rowcount
    conn.commit()
    click.echo(f"Cleanup: {items_deleted} items, {jobs_deleted} job_runs deleted (>{days} days)")


# ---------------------------------------------------------------------------
# entity-link
# ---------------------------------------------------------------------------

@cli.command("entity-link")
@click.option("--date", default=None, help="Date to run entity linking (YYYY-MM-DD)")
@click.option("--model", default=None, help="LLM model override")
def entity_link(date, model):
    """Run entity link pipeline, auto-migrating YAML entities on first run."""
    from datetime import date as date_cls
    from prism.pipeline.entity_link import run_entity_link
    from prism.pipeline.entities import migrate_yaml_to_db

    conn = get_connection(settings.db_path)
    link_date = date or date_cls.today().isoformat()

    # Auto-migrate YAML on first run if entity_config exists and no profiles yet
    if settings.entity_config.exists():
        existing = conn.execute("SELECT COUNT(*) FROM entity_profiles").fetchone()[0]
        if existing == 0:
            migrated = migrate_yaml_to_db(conn, settings.entity_config)
            if migrated:
                click.echo(f"Auto-migrated {migrated} entities from YAML")

    stats = run_entity_link(conn, link_date, model=model or settings.llm_model)
    click.echo(
        f"entity-link {link_date}: signals={stats['signals_processed']} "
        f"linked={stats['entities_linked']} created={stats['entities_created']} "
        f"staged={stats['entities_staged']} promoted={stats['candidates_promoted']}"
    )


# ---------------------------------------------------------------------------
# entity group
# ---------------------------------------------------------------------------

@cli.group()
def entity():
    """Manage tracked entities."""
    pass


@entity.command("list")
@click.option("--status", default=None,
              type=click.Choice(["emerging", "growing", "mature", "declining"]),
              help="Filter by lifecycle status")
@click.option("--category", default=None,
              type=click.Choice(["person", "org", "project", "model", "technique", "dataset"]),
              help="Filter by category")
def entity_list(status, category):
    """List entity profiles ordered by m7_score."""
    conn = get_connection(settings.db_path)

    clauses = []
    params: list = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if category:
        clauses.append("category = ?")
        params.append(category)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT display_name, category, status, m7_score, event_count_7d, needs_review "
        f"FROM entity_profiles {where} ORDER BY m7_score DESC",
        params,
    ).fetchall()

    if not rows:
        click.echo("No entities found.")
        return

    for r in rows:
        review = "  [needs review]" if r["needs_review"] else ""
        click.echo(
            f"  {r['display_name']:25s}  {r['category']:10s}  {r['status']:10s}  "
            f"m7={r['m7_score']:.1f}  events_7d={r['event_count_7d']}{review}"
        )


@entity.command("show")
@click.argument("name")
def entity_show(name):
    """Show profile details, aliases, and last 10 events for NAME."""
    from prism.pipeline.entity_normalize import normalize

    conn = get_connection(settings.db_path)
    name_norm = normalize(name)

    row = conn.execute(
        """
        SELECT ep.*
        FROM entity_aliases ea
        JOIN entity_profiles ep ON ea.entity_id = ep.id
        WHERE ea.alias_norm = ?
        LIMIT 1
        """,
        (name_norm,),
    ).fetchone()

    if row is None:
        # Try direct canonical_name lookup
        row = conn.execute(
            "SELECT * FROM entity_profiles WHERE canonical_name = ?",
            (name_norm,),
        ).fetchone()

    if row is None:
        click.echo(f"Entity not found: {name}")
        return

    click.echo(f"\n=== {row['display_name']} ===")
    click.echo(f"  category : {row['category']}")
    click.echo(f"  status   : {row['status']}")
    click.echo(f"  m7_score : {row['m7_score']:.2f}")
    click.echo(f"  events   : 7d={row['event_count_7d']}  30d={row['event_count_30d']}  total={row['event_count_total']}")
    if row["summary"]:
        click.echo(f"  summary  : {row['summary']}")

    # Aliases
    aliases = conn.execute(
        "SELECT surface_form, source FROM entity_aliases WHERE entity_id = ? ORDER BY source",
        (row["id"],),
    ).fetchall()
    if aliases:
        click.echo(f"\nAliases ({len(aliases)}):")
        for a in aliases:
            click.echo(f"  [{a['source']}] {a['surface_form']}")

    # Last 10 events
    events = conn.execute(
        """
        SELECT ee.date, ee.event_type, ee.impact, ee.description
        FROM entity_events ee
        WHERE ee.entity_id = ?
        ORDER BY ee.date DESC, ee.id DESC
        LIMIT 10
        """,
        (row["id"],),
    ).fetchall()
    if events:
        click.echo(f"\nLast {len(events)} events:")
        for e in events:
            desc = (e["description"] or "")[:80]
            click.echo(f"  {e['date']}  {e['event_type']:10s}  {e['impact']:6s}  {desc}")


# ---------------------------------------------------------------------------
# practice
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("note")
def practice(note):
    """Record a manual practice note as a raw item."""
    from datetime import datetime
    from prism.db import get_source_by_key, insert_source, insert_raw_item

    conn = get_connection(settings.db_path)

    source_key = "practice:manual"
    source_row = get_source_by_key(conn, source_key)
    if source_row is None:
        source_id = insert_source(
            conn, source_key=source_key, type="manual",
            handle="manual", origin="cli"
        )
    else:
        source_id = source_row["id"]

    url = f"practice:{datetime.now().isoformat()}"
    item_id = insert_raw_item(
        conn, source_id=source_id, url=url,
        title=note, body=note,
    )
    if item_id:
        click.echo(f"Saved practice note (id={item_id}): {note[:80]}")
    else:
        click.echo("Note already recorded.")


@cli.command("process-external-feeds")
def process_external_feeds_cmd():
    """Process pending external_feeds rows: LLM extract + propose sources."""
    from prism.pipeline.external_feed import run_external_feed_consumer

    db_path = Path(os.environ.get("PRISM_DB_PATH", str(settings.db_path)))
    conn = get_connection(db_path)
    try:
        n = run_external_feed_consumer(conn)
    finally:
        conn.close()
    click.echo(f"Processed {n} external feed(s).")


@cli.group("sources")
def sources_group():
    """Source configuration tools."""


@sources_group.command("prune")
@click.option("--threshold", type=float, default=-5.0,
              help="Prune sources with preference weight below this.")
@click.option("--dry-run", is_flag=True, help="Show proposed changes without writing.")
@click.option("--yes", is_flag=True, help="Apply without prompting.")
def sources_prune_cmd(threshold: float, dry_run: bool, yes: bool):
    """Comment out sources.yaml entries whose preference weight is below threshold."""
    import json
    from datetime import date
    from prism.pipeline.external_feed import _sources_yaml_path
    from prism.sources.yaml_editor import load_sources_list, comment_out_source, _source_key

    yaml_path = _sources_yaml_path()
    db_path = Path(os.environ.get("PRISM_DB_PATH", str(settings.db_path)))
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT key, weight FROM preference_weights "
            "WHERE dimension = 'source' AND weight < ? ORDER BY weight ASC",
            (threshold,),
        ).fetchall()

        if not rows:
            click.echo(f"No sources below threshold {threshold}.")
            return

        current = {_source_key(s): s for s in load_sources_list(yaml_path)}
        to_prune = [(k, w) for k, w in rows if k in current]

        if not to_prune:
            click.echo("No matching sources in yaml.")
            return

        click.echo("Candidates to prune:")
        for k, w in to_prune:
            click.echo(f"  {k}  weight={w:.1f}")

        if dry_run:
            click.echo("(dry-run; no changes written)")
            return

        if not yes:
            if not click.confirm(f"Prune {len(to_prune)} source(s)?", default=False):
                click.echo("Aborted.")
                return

        today = date.today().isoformat()
        pruned = 0
        for k, w in to_prune:
            if comment_out_source(yaml_path, k, reason=f"pruned weight={w:.1f} {today}"):
                pruned += 1
                conn.execute(
                    "INSERT INTO decision_log (layer, action, reason, context_json) "
                    "VALUES ('recall', 'prune_source', ?, ?)",
                    (f"pruned {k} (weight={w:.1f})",
                     json.dumps({"weight": w, "source_key": k})),
                )
                conn.commit()
        click.echo(f"Pruned {pruned} source(s) in {yaml_path}.")
    finally:
        conn.close()


@cli.command("quality-scan")
def quality_scan():
    """Capture health metrics and evaluate anomaly rules."""
    from prism.quality import scan
    from prism.quality.rules import list_open
    conn = get_connection(settings.db_path)
    try:
        result = scan(conn)
        click.echo(
            f"Quality scan: {result['metrics']} metrics, "
            f"{result['rules']} rules evaluated."
        )
        open_anomalies = list_open(conn)
        if not open_anomalies:
            click.echo("No open anomalies.")
            return
        click.echo(f"Open anomalies ({len(open_anomalies)}):")
        for a in open_anomalies:
            click.echo(f"  [{a['severity']}] {a['title']} — {a['detail']}")
    finally:
        conn.close()
