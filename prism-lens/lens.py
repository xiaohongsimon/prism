#!/usr/bin/env python3
"""prism-lens — query helper for the Claude Code skill.

Reads the local Prism SQLite DB and emits JSON the skill can hand to
Claude for synthesis. No LLM calls here; all intelligence stays in
the invoking Claude Code session.

Commands:
    topic "<query>"        — FTS match across raw_items + signals, last N days
    daily                  — top N items of last 24h, ranked by CTR score
    creator <source_key>   — one creator's recent output + related items

Output: JSON on stdout. Errors to stderr, exit code non-zero on failure.

DB path resolution:
    1. --db <path>
    2. $PRISM_DB_PATH
    3. ../data/prism.sqlite3  (relative to this file, the in-repo default)
    4. ~/.prism/prism.sqlite3
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Optional

MAX_ITEMS = 40
MAX_DAYS = 30


def resolve_db_path(override: Optional[str]) -> Path:
    if override:
        return Path(override).expanduser()
    env = os.environ.get("PRISM_DB_PATH")
    if env:
        return Path(env).expanduser()
    here = Path(__file__).resolve().parent
    for cand in [
        here.parent / "data" / "prism.sqlite3",
        Path("~/.prism/prism.sqlite3").expanduser(),
    ]:
        if cand.exists():
            return cand
    return here.parent / "data" / "prism.sqlite3"  # last-resort, will error


def open_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        print(
            f"prism-lens: DB not found at {db_path}. "
            "Set PRISM_DB_PATH or run `prism sync` first.",
            file=sys.stderr,
        )
        sys.exit(2)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


_FTS_QUOTE_RE = re.compile(r'[^\w\u4e00-\u9fff\s]')


def fts_query(text: str) -> str:
    """Build a safe FTS5 query from user text.

    Tokenize on whitespace, drop punctuation, AND-join the terms. This
    keeps zero-result queries uncommon without letting odd characters
    break FTS syntax.
    """
    cleaned = _FTS_QUOTE_RE.sub(" ", text.strip())
    tokens = [t for t in cleaned.split() if t]
    if not tokens:
        return '""'
    return " ".join(f'"{t}"' for t in tokens)


def extract_via(raw_json: str) -> str:
    """Pull the `via` marker out of raw_json (x_home / youtube_home / github_home)."""
    try:
        return (json.loads(raw_json) or {}).get("via", "") or ""
    except (ValueError, TypeError):
        return ""


def cmd_topic(conn: sqlite3.Connection, query: str, days: int, limit: int) -> dict:
    """FTS match raw_items.body/title AND signals.summary over the last N days."""
    fts = fts_query(query)
    cutoff = f"datetime('now', '-{int(days)} days')"

    # Raw item matches
    item_rows = conn.execute(
        f"""
        SELECT ri.id, ri.url, ri.title, ri.body, ri.body_zh, ri.author,
               ri.published_at, ri.raw_json, ri.created_at,
               s.source_key, s.type AS source_type, s.handle AS source_handle
        FROM item_search
        JOIN raw_items ri ON ri.id = item_search.rowid
        JOIN sources s    ON s.id = ri.source_id
        WHERE item_search MATCH ?
          AND ri.created_at >= {cutoff}
        ORDER BY ri.created_at DESC
        LIMIT ?
        """,
        (fts, limit),
    ).fetchall()

    # Signal (LLM-analyzed cluster) matches
    sig_rows = conn.execute(
        f"""
        SELECT sg.id, sg.summary, sg.content_zh, sg.why_it_matters,
               sg.tl_perspective, sg.signal_layer, sg.signal_strength,
               sg.tags_json, sg.created_at, sg.cluster_id
        FROM signal_search
        JOIN signals sg ON sg.id = signal_search.rowid
        WHERE signal_search MATCH ?
          AND sg.created_at >= {cutoff}
          AND sg.is_current = 1
        ORDER BY sg.signal_strength DESC, sg.created_at DESC
        LIMIT ?
        """,
        (fts, limit),
    ).fetchall()

    items = [
        {
            "id": r["id"],
            "url": r["url"],
            "title": r["title"],
            "body": (r["body_zh"] or r["body"] or "")[:1200],
            "author": r["author"],
            "published_at": r["published_at"],
            "source_key": r["source_key"],
            "source_type": r["source_type"],
            "source_handle": r["source_handle"],
            "via": extract_via(r["raw_json"] or "{}"),
        }
        for r in item_rows
    ]
    signals = [
        {
            "id": r["id"],
            "summary": r["summary"],
            "summary_zh": r["content_zh"],
            "why_it_matters": r["why_it_matters"],
            "tl_perspective": r["tl_perspective"],
            "signal_layer": r["signal_layer"],
            "signal_strength": r["signal_strength"],
            "tags": json.loads(r["tags_json"] or "[]"),
            "cluster_id": r["cluster_id"],
            "created_at": r["created_at"],
        }
        for r in sig_rows
    ]
    return {
        "mode": "topic",
        "query": query,
        "window_days": days,
        "raw_item_matches": items,
        "signal_matches": signals,
    }


def cmd_daily(conn: sqlite3.Connection, hours: int, limit: int) -> dict:
    """Top analyzed signals of the last N hours, ranked by signal_strength."""
    cutoff = f"datetime('now', '-{int(hours)} hours')"
    rows = conn.execute(
        f"""
        SELECT sg.id, sg.summary, sg.content_zh, sg.why_it_matters,
               sg.tl_perspective, sg.signal_layer, sg.signal_strength,
               sg.tags_json, sg.created_at, sg.cluster_id,
               COALESCE(ss.bt_score, 1500.0) AS bt_score
        FROM signals sg
        LEFT JOIN signal_scores ss ON ss.signal_id = sg.id
        WHERE sg.created_at >= {cutoff}
          AND sg.is_current = 1
          AND sg.signal_layer IN ('strategic', 'actionable')
        ORDER BY sg.signal_strength DESC, ss.bt_score DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    signals = []
    for r in rows:
        # Pull representative raw_items in this cluster, capped at 3
        src_rows = conn.execute(
            """
            SELECT ri.url, ri.title, ri.author, ri.raw_json,
                   s.source_key, s.type AS source_type, s.handle AS source_name
            FROM cluster_items ci
            JOIN raw_items ri ON ri.id = ci.raw_item_id
            JOIN sources s    ON s.id = ri.source_id
            WHERE ci.cluster_id = ?
            LIMIT 3
            """,
            (r["cluster_id"],),
        ).fetchall()
        signals.append({
            "id": r["id"],
            "summary": r["summary"],
            "summary_zh": r["content_zh"],
            "why_it_matters": r["why_it_matters"],
            "tl_perspective": r["tl_perspective"],
            "signal_layer": r["signal_layer"],
            "signal_strength": r["signal_strength"],
            "bt_score": r["bt_score"],
            "tags": json.loads(r["tags_json"] or "[]"),
            "sources": [
                {
                    "url": s["url"],
                    "title": s["title"],
                    "author": s["author"],
                    "source_key": s["source_key"],
                    "source_name": s["source_name"],
                    "via": extract_via(s["raw_json"] or "{}"),
                }
                for s in src_rows
            ],
        })
    return {
        "mode": "daily",
        "window_hours": hours,
        "signals": signals,
    }


def cmd_creator(conn: sqlite3.Connection, source_key: str, days: int, limit: int) -> dict:
    """Recent output from one source + the signals it contributed to."""
    src = conn.execute(
        "SELECT id, source_key, type, handle FROM sources WHERE source_key = ?",
        (source_key,),
    ).fetchone()
    if src is None:
        return {"mode": "creator", "error": f"unknown source_key: {source_key}"}

    cutoff = f"datetime('now', '-{int(days)} days')"
    item_rows = conn.execute(
        f"""
        SELECT ri.id, ri.url, ri.title, ri.body, ri.body_zh, ri.author,
               ri.published_at, ri.raw_json, ri.created_at
        FROM raw_items ri
        WHERE ri.source_id = ?
          AND ri.created_at >= {cutoff}
        ORDER BY ri.created_at DESC
        LIMIT ?
        """,
        (src["id"], limit),
    ).fetchall()

    return {
        "mode": "creator",
        "source": {
            "source_key": src["source_key"],
            "type": src["type"],
            "handle": src["handle"],
        },
        "window_days": days,
        "items": [
            {
                "url": r["url"],
                "title": r["title"],
                "body": (r["body_zh"] or r["body"] or "")[:1200],
                "author": r["author"],
                "published_at": r["published_at"],
                "via": extract_via(r["raw_json"] or "{}"),
            }
            for r in item_rows
        ],
    }


def cmd_sources(conn: sqlite3.Connection) -> dict:
    """List the user's configured sources so Claude can suggest `creator` queries."""
    rows = conn.execute(
        """
        SELECT source_key, type, handle, enabled
        FROM sources
        WHERE enabled = 1
        ORDER BY type, source_key
        """
    ).fetchall()
    return {
        "mode": "sources",
        "sources": [dict(r) for r in rows],
    }


def main() -> int:
    ap = argparse.ArgumentParser(prog="prism-lens", description=__doc__)
    ap.add_argument("--db", default=None, help="Path to prism.sqlite3")
    ap.add_argument("--days", type=int, default=MAX_DAYS, help="Lookback window in days")
    ap.add_argument("--limit", type=int, default=MAX_ITEMS, help="Max items to return")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_topic = sub.add_parser("topic", help="FTS search by topic")
    p_topic.add_argument("query", help="Topic / keyword(s)")

    p_daily = sub.add_parser("daily", help="Top recent signals")
    p_daily.add_argument("--hours", type=int, default=24)

    p_creator = sub.add_parser("creator", help="Single creator's recent items")
    p_creator.add_argument("source_key")

    sub.add_parser("sources", help="List all active sources")

    args = ap.parse_args()
    db_path = resolve_db_path(args.db)
    conn = open_db(db_path)

    if args.cmd == "topic":
        out = cmd_topic(conn, args.query, args.days, args.limit)
    elif args.cmd == "daily":
        out = cmd_daily(conn, args.hours, args.limit)
    elif args.cmd == "creator":
        out = cmd_creator(conn, args.source_key, args.days, args.limit)
    elif args.cmd == "sources":
        out = cmd_sources(conn)
    else:
        print(f"unknown command: {args.cmd}", file=sys.stderr)
        return 2

    out["_db"] = str(db_path)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
