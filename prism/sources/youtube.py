"""YouTube channel source adapter using Atom RSS feeds.

Fetches Atom feeds from YouTube for configured channel IDs,
parses the feed, and filters videos published within the last 48 hours.
"""

import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import httpx

from prism.models import RawItem
from prism.sources.base import SyncResult

logger = logging.getLogger(__name__)

_YT_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
_LOOKBACK_HOURS = 48

# Atom + YouTube XML namespaces
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def _parse_dt(dt_str: str) -> datetime | None:
    """Parse ISO-8601 datetime string, returning timezone-aware datetime or None."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_recent(dt_str: str, hours: int = _LOOKBACK_HOURS) -> bool:
    """Return True if the datetime string is within the last N hours."""
    dt = _parse_dt(dt_str)
    if dt is None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt >= cutoff


def parse_youtube_feed(xml_text: str, channel_id: str, lookback_hours: int = _LOOKBACK_HOURS) -> list[RawItem]:
    """Parse YouTube Atom feed XML into a list of RawItem.

    Filters to videos published within the last N hours.
    """
    root = ET.fromstring(xml_text)
    items: list[RawItem] = []

    for entry in root.findall("atom:entry", _NS):
        # <yt:videoId>
        video_id = (entry.findtext("yt:videoId", "", _NS) or "").strip()

        # <published>
        published = (entry.findtext("atom:published", "", _NS) or "").strip()
        if not _is_recent(published, hours=lookback_hours):
            continue

        # <title>
        title = (entry.findtext("atom:title", "", _NS) or "").strip()

        # <link rel="alternate" href="...">
        link_el = entry.find("atom:link[@rel='alternate']", _NS)
        if link_el is None:
            link_el = entry.find("atom:link", _NS)
        url = (link_el.get("href", "") if link_el is not None else "").strip()

        # <author><name>
        author_el = entry.find("atom:author", _NS)
        author = ""
        if author_el is not None:
            author = (author_el.findtext("atom:name", "", _NS) or "").strip()

        # <media:group><media:description>
        description = ""
        media_group = entry.find("media:group", _NS)
        if media_group is not None:
            description = (media_group.findtext("media:description", "", _NS) or "").strip()

        items.append(
            RawItem(
                url=url,
                title=title,
                body=description,
                author=author,
                raw_json=json.dumps(
                    {
                        "channel_id": channel_id,
                        "video_id": video_id,
                        "published": published,
                    },
                    ensure_ascii=False,
                ),
            )
        )
    return items


class YoutubeAdapter:
    """Source adapter for YouTube channels via Atom RSS feed."""

    async def sync(self, config: dict) -> SyncResult:
        """Fetch and parse YouTube channel feeds.

        Config keys:
            key (str): source key used in SyncResult
            channels (list[str]): YouTube channel IDs
            lookback_hours (int): hours to look back for videos (default: 48)
        """
        source_key = config.get("key", "youtube:channels")
        channels: list[str] = config.get("channels", [])
        lookback_hours = int(config.get("lookback_hours", _LOOKBACK_HOURS))

        items: list[RawItem] = []

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                for channel_id in channels:
                    feed_url = _YT_FEED_URL.format(channel_id=channel_id)
                    try:
                        resp = await client.get(feed_url)
                        if resp.status_code in (404, 403):
                            logger.warning("Skipping channel %s: %s", channel_id, resp.status_code)
                            continue
                        resp.raise_for_status()
                        channel_items = parse_youtube_feed(
                            resp.text,
                            channel_id=channel_id,
                            lookback_hours=lookback_hours,
                        )
                        items.extend(channel_items)
                    except httpx.HTTPStatusError:
                        logger.warning("Skipping channel %s: HTTP error", channel_id)
                        continue

            return SyncResult(
                source_key=source_key,
                items=items,
                success=True,
                stats={"channels": len(channels), "videos_found": len(items)},
            )

        except Exception as e:
            logger.exception("YouTube adapter sync failed")
            return SyncResult(
                source_key=source_key,
                items=[],
                success=False,
                error=str(e),
            )
