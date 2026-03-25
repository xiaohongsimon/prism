"""Prism CLI — Click-based command interface."""

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
def analyze(incremental, daily, date):
    """Run LLM signal analysis."""
    from prism.pipeline.analyze import run_incremental_analysis, run_daily_analysis
    conn = get_connection(settings.db_path)

    if not incremental and not daily:
        click.echo("Specify --incremental or --daily")
        return

    if incremental:
        count = run_incremental_analysis(conn, model=settings.llm_cheap_model)
        click.echo(f"Incremental analysis: {count} signals created")

    if daily:
        from datetime import date as date_cls
        analysis_date = date or date_cls.today().isoformat()
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
