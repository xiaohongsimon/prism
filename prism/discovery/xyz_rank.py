"""Discover candidate head podcasts via Apple Podcasts CN top chart.

Rationale: xiaoyuzhou has no public rank/search API. Apple's Marketing Tools
RSS exposes a region-scoped Top Podcasts feed for free. Most popular
xiaoyuzhou podcasts also appear there (e.g. 知行小酒馆 consistently in top 10).

Pipeline:
  1. `sync_rank(conn)` fetches CN top-50 podcasts from Apple RSS.
  2. Upserts into `xyz_rank_candidate` (apple_id keyed).
  3. Marks each row's `subscribed=1` if its name matches an existing
     `sources.type='xiaoyuzhou'` entry in `sources.handle`.
  4. Board surfaces the unsubscribed rows as 🏆 候选播客.

The user then picks → pastes the xiaoyuzhou URL → `/sources/add-xyz` route
appends it to sources.yaml and triggers discover.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

APPLE_URL = "https://rss.applemarketingtools.com/api/v2/cn/podcasts/top/50/podcasts.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _fetch_apple_cn_top(limit: int = 50) -> list[dict[str, Any]]:
    req = urllib.request.Request(APPLE_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    results = (data.get("feed") or {}).get("results") or []
    return results[:limit]


def sync_rank(conn: sqlite3.Connection, limit: int = 50) -> dict[str, int]:
    """Fetch Apple CN top N, upsert xyz_rank_candidate, mark subscribed."""
    items = _fetch_apple_cn_top(limit=limit)
    added = 0
    updated = 0

    # Subscribed names (xiaoyuzhou type, enabled or disabled both count — we
    # don't want to re-surface something user already decided on).
    subscribed_names = {
        row[0].strip()
        for row in conn.execute(
            "SELECT handle FROM sources WHERE type='xiaoyuzhou' AND handle != ''"
        ).fetchall()
    }

    for i, ep in enumerate(items, start=1):
        apple_id = str(ep.get("id") or "")
        name = (ep.get("name") or "").strip()
        artist = (ep.get("artistName") or "").strip()
        artwork = ep.get("artworkUrl100") or ""
        if not apple_id or not name:
            continue
        subscribed = 1 if name in subscribed_names else 0

        existing = conn.execute(
            "SELECT apple_id FROM xyz_rank_candidate WHERE apple_id=?",
            (apple_id,),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE xyz_rank_candidate
                   SET name=?, artist=?, rank=?, artwork_url=?, subscribed=?,
                       last_seen_at=datetime('now')
                   WHERE apple_id=?""",
                (name, artist, i, artwork, subscribed, apple_id),
            )
            updated += 1
        else:
            conn.execute(
                """INSERT INTO xyz_rank_candidate
                   (apple_id, name, artist, rank, artwork_url, subscribed)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (apple_id, name, artist, i, artwork, subscribed),
            )
            added += 1

    conn.commit()
    return {"fetched": len(items), "added": added, "updated": updated}
