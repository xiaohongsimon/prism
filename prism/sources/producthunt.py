"""Product Hunt source adapter using Atom feed.

Fetches latest products from Product Hunt's public RSS/Atom feed.
"""

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from prism.models import RawItem
from prism.sources.base import SyncResult

logger = logging.getLogger(__name__)

ATOM_NS = "http://www.w3.org/2005/Atom"
PH_FEED_URL = "https://www.producthunt.com/feed"


class ProductHuntAdapter:
    """Source adapter for Product Hunt."""

    async def sync(self, config: dict) -> SyncResult:
        source_key = config.get("key", "ph:daily")
        max_items = int(config.get("max_items", 10))

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(PH_FEED_URL)
                resp.raise_for_status()

            root = ET.fromstring(resp.text)
            items = []

            for entry in root.findall(f"{{{ATOM_NS}}}entry")[:max_items]:
                title = (entry.findtext(f"{{{ATOM_NS}}}title") or "").strip()
                link_el = entry.find(f"{{{ATOM_NS}}}link[@rel='alternate']")
                url = link_el.get("href", "") if link_el is not None else ""
                if not url:
                    url = (entry.findtext(f"{{{ATOM_NS}}}id") or "").strip()
                content = (entry.findtext(f"{{{ATOM_NS}}}content") or "").strip()
                published = (entry.findtext(f"{{{ATOM_NS}}}published") or "").strip()
                author = (entry.findtext(f"{{{ATOM_NS}}}author/{{{ATOM_NS}}}name") or "").strip()

                # Parse published date
                pub_dt = None
                if published:
                    try:
                        pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pass

                body = title
                if content:
                    clean = re.sub(r"<[^>]+>", "", content).strip()
                    if clean:
                        body = f"{title}\n\n{clean}"

                items.append(RawItem(
                    url=url,
                    title=title,
                    body=body,
                    author=author,
                    published_at=pub_dt,
                    raw_json=json.dumps({"source": "producthunt"}, ensure_ascii=False),
                ))

            return SyncResult(
                source_key=source_key,
                items=items,
                success=True,
                stats={"fetched": len(items)},
            )

        except Exception as e:
            logger.exception("Product Hunt adapter sync failed")
            return SyncResult(
                source_key=source_key, items=[], success=False, error=str(e),
            )
