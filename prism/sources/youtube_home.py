"""YouTube home/recommended feed source adapter via yt-dlp.

Unlike prism/sources/youtube.py which pulls public Atom RSS feeds from
specific channels, this adapter pulls videos that YouTube itself
recommends to the logged-in user. The signal is strong: every video
is one YouTube chose for *me* based on my watch history, subscriptions,
and search behavior. Prism ingests them as raw_items; `via="youtube_home"`
in raw_json lets ranking upweight them later.

Auth via browser cookies: `yt-dlp --cookies-from-browser chrome` reads
Chrome's cookie jar directly. Requires Chrome logged in to YouTube on
this machine. All failures return SyncResult(success=False); never raises.

Body is left empty here — the existing `prism enrich-youtube` job will
backfill subtitles asynchronously (see cli.py:240).
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from typing import Optional

from prism.models import RawItem
from prism.sources.base import SyncResult

logger = logging.getLogger(__name__)

# feed slug → YouTube URL path
_FEED_URLS = {
    "recommended": "https://www.youtube.com/feed/recommended",
    "subscriptions": "https://www.youtube.com/feed/subscriptions",
    "trending": "https://www.youtube.com/feed/trending",
}


async def run_yt_dlp_feed(
    *,
    feed: str = "recommended",
    count: int = 30,
    browser: str = "chrome",
    timeout_s: int = 90,
) -> tuple[Optional[dict], str]:
    """Call `yt-dlp --cookies-from-browser <browser> --flat-playlist -J <feed_url>`.

    Returns (playlist_dict_or_None, error_message). Never raises.
    """
    if not shutil.which("yt-dlp"):
        return None, "yt-dlp not installed (brew install yt-dlp)"

    url = _FEED_URLS.get(feed)
    if not url:
        return None, f"unknown feed slug: {feed} (must be one of {list(_FEED_URLS)})"

    cmd = [
        "yt-dlp",
        "--cookies-from-browser", browser,
        "--flat-playlist",
        "--playlist-end", str(count),
        "-J",
        "--no-warnings",
        url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:  # noqa: BLE001
        return None, f"yt-dlp subprocess spawn failed: {e}"

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        return None, f"yt-dlp timed out after {timeout_s}s"

    if proc.returncode != 0:
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        low = err.lower()
        if "cookie" in low or "sign in" in low or "login required" in low:
            return None, "YouTube cookies missing/expired (log in to YouTube in " + browser + ")"
        head = err.splitlines()[:6]
        return None, f"yt-dlp exited {proc.returncode}: {' | '.join(head)[:300]}"

    text = (stdout or b"").decode("utf-8", errors="replace").strip()
    if not text:
        return None, "yt-dlp returned empty stdout"

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"yt-dlp returned non-JSON: {e}"

    if not isinstance(data, dict):
        return None, f"yt-dlp returned non-object JSON: {type(data).__name__}"
    return data, ""


def parse_home_entries(playlist: dict, feed_slug: str) -> list[RawItem]:
    """Convert yt-dlp playlist JSON into RawItem rows.

    Skips Shorts (duration < 60s or /shorts/ URL) since they're too
    brief for meaningful downstream analysis.
    """
    entries = playlist.get("entries") or []
    items: list[RawItem] = []

    for e in entries:
        if not isinstance(e, dict):
            continue
        vid = (e.get("id") or "").strip()
        url = (e.get("url") or "").strip()
        title = (e.get("title") or "").strip()
        if not vid or not url or not title:
            continue
        if "/shorts/" in url:
            continue
        duration = e.get("duration")
        if isinstance(duration, (int, float)) and duration > 0 and duration < 60:
            continue

        # Pick highest-res thumbnail as poster
        thumbs = e.get("thumbnails") or []
        poster = ""
        if isinstance(thumbs, list) and thumbs:
            # Prefer the largest by width
            best = max(
                (t for t in thumbs if isinstance(t, dict) and t.get("url")),
                key=lambda t: t.get("width", 0) or 0,
                default=None,
            )
            if best:
                poster = best.get("url", "")

        raw_data = {
            "video_id": vid,
            "duration_s": duration,
            "thumbnail": poster,
            "feed": feed_slug,
            "via": "youtube_home",  # ranking marker
        }

        items.append(
            RawItem(
                url=url,
                title=title,
                body="",  # enrich-youtube backfills subtitles later
                author="",  # yt-dlp --flat-playlist doesn't expose channel
                published_at=None,
                raw_json=json.dumps(raw_data, ensure_ascii=False),
            )
        )
    return items


class YoutubeHomeAdapter:
    """Source adapter for YouTube recommended/subscriptions/trending feeds."""

    async def sync(self, config: dict) -> SyncResult:
        source_key = config.get("source_key", "youtube_home:recommended")
        feed = config.get("feed", "recommended")
        count = int(config.get("count", 30))
        browser = config.get("browser", "chrome")

        playlist, err = await run_yt_dlp_feed(feed=feed, count=count, browser=browser)
        if playlist is None:
            return SyncResult(
                source_key=source_key,
                items=[],
                success=False,
                error=err,
            )

        items = parse_home_entries(playlist, feed_slug=feed)
        return SyncResult(source_key=source_key, items=items, success=True)
