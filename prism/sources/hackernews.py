"""Hacker News /best source adapter using RSS feed.

Fetches RSS from hnrss.org/best, parses standard RSS 2.0 XML,
and returns the top N items.
"""

import json
import logging
import re
import xml.etree.ElementTree as ET

import httpx

from prism.models import RawItem
from prism.sources.base import SyncResult

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return _HTML_TAG_RE.sub("", text).strip()


def parse_hn_rss(xml_text: str, max_items: int = 15) -> list[RawItem]:
    """Parse HN RSS 2.0 XML into a list of RawItem.

    Expects standard <rss><channel><item> format from hnrss.org.
    """
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []

    items: list[RawItem] = []
    for item_el in channel.findall("item")[:max_items]:
        title = (item_el.findtext("title") or "").strip()
        link = (item_el.findtext("link") or "").strip()
        description = _strip_html(item_el.findtext("description") or "")
        pub_date = (item_el.findtext("pubDate") or "").strip()

        items.append(
            RawItem(
                url=link,
                title=title,
                body=description,
                author="",
                raw_json=json.dumps(
                    {"title": title, "link": link, "pubDate": pub_date},
                    ensure_ascii=False,
                ),
            )
        )
    return items


class HackernewsAdapter:
    """Source adapter for Hacker News /best via hnrss.org RSS."""

    async def sync(self, config: dict) -> SyncResult:
        """Fetch and parse HN /best RSS feed.

        Config keys:
            key (str): source key used in SyncResult
            feed_url (str): RSS URL (default: https://hnrss.org/best)
            max_items (int): maximum items to return (default: 15)
        """
        source_key = config.get("key", "hn:best")
        feed_url = config.get("feed_url", "https://hnrss.org/best")
        max_items = int(config.get("max_items", 15))

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(feed_url)
                resp.raise_for_status()

            items = parse_hn_rss(resp.text, max_items=max_items)

            return SyncResult(
                source_key=source_key,
                items=items,
                success=True,
                stats={"fetched": len(items)},
            )

        except Exception as e:
            logger.exception("HN adapter sync failed")
            return SyncResult(
                source_key=source_key,
                items=[],
                success=False,
                error=str(e),
            )
