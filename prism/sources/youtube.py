"""YouTube channel source adapter using Atom RSS feeds.

Fetches Atom feeds from YouTube for configured channel IDs,
parses the feed, and filters videos published within the last 48 hours.
"""

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import httpx

from prism.models import RawItem
from prism.sources.base import SyncResult
import prism.sources.subtitles as _subtitles_mod

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


_YT_BOILERPLATE_PATTERNS = [
    re.compile(r"付费频道订阅[：:]\s*https?://\S+", re.IGNORECASE),
    re.compile(r"成为此频道的会员即可获享以下福利[：:]\s*https?://\S+", re.IGNORECASE),
    re.compile(r"欢迎加入Discord讨论服务器[：:]\s*https?://\S+", re.IGNORECASE),
    re.compile(r"https?://(?:www\.)?youtube\.com/channel/\S+/join\b"),
    re.compile(r"https?://(?:www\.)?discord\.gg/\S+"),
]


def _clean_youtube_body(body: str) -> str:
    """Remove YouTube boilerplate (membership links, Discord links, etc.)."""
    if not body:
        return body
    for pat in _YT_BOILERPLATE_PATTERNS:
        body = pat.sub("", body)
    # Collapse whitespace
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body


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
            channel_id (str): single YouTube channel ID (alternative to channels)
            channels (list[str]): multiple YouTube channel IDs (ignored if channel_id set)
            lookback_hours (int): hours to look back for videos (default: 48)
        """
        source_key = config.get("key", "youtube:channels")
        single_channel = config.get("channel_id")
        if single_channel:
            channels = [single_channel]
        else:
            channels = config.get("channels", [])
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

            # Clean YouTube boilerplate from body (membership links, Discord, etc.)
            for item in items:
                item.body = _clean_youtube_body(item.body)

            # Filter out Shorts (too short for meaningful analysis)
            regular_items = [i for i in items if i.url and "/shorts/" not in i.url]
            shorts_count = len(items) - len(regular_items)

            # Enrich regular videos with subtitle transcript
            enriched = 0
            for item in regular_items:
                try:
                    transcript = _subtitles_mod.extract_subtitles(item.url)
                    if transcript and len(transcript) > len(item.body):
                        item.body = transcript[:8000]  # Cap at 8k chars
                        enriched += 1
                except Exception as exc:
                    logger.warning("Subtitle extraction failed for %s: %s", item.url, exc)

            return SyncResult(
                source_key=source_key,
                items=regular_items,
                success=True,
                stats={"channels": len(channels), "videos_found": len(regular_items),
                       "shorts_filtered": shorts_count, "subtitles_enriched": enriched},
            )

        except Exception as e:
            logger.exception("YouTube adapter sync failed")
            return SyncResult(
                source_key=source_key,
                items=[],
                success=False,
                error=str(e),
            )
