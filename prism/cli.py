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
