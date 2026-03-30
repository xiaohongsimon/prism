"""Expand short links in tweet bodies and enrich with YouTube transcripts."""

import logging
import re
import sqlite3

import httpx

from prism.sources.subtitles import extract_subtitles

logger = logging.getLogger(__name__)

_TCO_RE = re.compile(r"https://t\.co/\w+")
_YT_RE = re.compile(r"https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]+)")


def _resolve_url(short_url: str) -> str | None:
    """Follow redirects to get final URL."""
    try:
        with httpx.Client(follow_redirects=True, timeout=10) as client:
            resp = client.head(short_url)
            return str(resp.url)
    except Exception:
        return None


def enrich_item_links(conn: sqlite3.Connection, item_id: int) -> bool:
    """Resolve t.co links in an item's body. If YouTube found, fetch transcript.

    Returns True if item body was enriched.
    """
    row = conn.execute("SELECT body FROM raw_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        return False

    body = row["body"]
    tco_links = _TCO_RE.findall(body)
    if not tco_links:
        return False

    for short_url in tco_links:
        resolved = _resolve_url(short_url)
        if not resolved:
            continue

        # Replace short link with resolved URL in body
        body = body.replace(short_url, resolved)

        # If it's YouTube, fetch transcript
        yt_match = _YT_RE.search(resolved)
        if yt_match:
            logger.info("Found YouTube link in item %d: %s", item_id, resolved)
            transcript = extract_subtitles(resolved)
            if transcript and len(transcript) > len(body):
                body = body + "\n\n--- 视频字幕 ---\n\n" + transcript[:4000]
                logger.info("Enriched item %d with %d chars transcript", item_id, len(transcript))

    if body != row["body"]:
        conn.execute("UPDATE raw_items SET body = ? WHERE id = ?", (body[:8000], item_id))
        conn.commit()
        return True
    return False


def batch_enrich_links(conn: sqlite3.Connection, limit: int = 20) -> int:
    """Resolve links for items with short t.co URLs and short body."""
    rows = conn.execute(
        """
        SELECT ri.id, ri.body FROM raw_items ri
        JOIN sources s ON s.id = ri.source_id
        WHERE (s.type = 'follow_builders' OR s.type = 'x')
          AND ri.body LIKE '%t.co/%'
          AND LENGTH(ri.body) < 500
        ORDER BY ri.id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()

    enriched = 0
    for row in rows:
        if enrich_item_links(conn, row["id"]):
            enriched += 1
    return enriched
