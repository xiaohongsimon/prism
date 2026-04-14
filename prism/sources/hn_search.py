"""Hacker News keyword search via Algolia API.

Searches HN stories by keyword, returns recent results.
Complements hn:best by capturing topic-specific content.
"""

import json
import logging
from datetime import datetime, timezone, timedelta

import httpx

from prism.models import RawItem
from prism.sources.base import SyncResult

logger = logging.getLogger(__name__)

ALGOLIA_URL = "https://hn.algolia.com/api/v1/search"


class HnSearchAdapter:
    """Source adapter for HN keyword search via Algolia."""

    async def sync(self, config: dict) -> SyncResult:
        source_key = config.get("key", "hn:search")
        queries = config.get("queries", [])
        max_per_query = int(config.get("max_per_query", 10))
        lookback_hours = int(config.get("lookback_hours", 48))

        if not queries:
            return SyncResult(source_key=source_key, items=[], success=True)

        cutoff = int((datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).timestamp())
        seen_urls = set()
        all_items = []

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                for query in queries:
                    params = {
                        "query": query,
                        "tags": "story",
                        "numericFilters": f"created_at_i>{cutoff}",
                        "hitsPerPage": max_per_query,
                    }
                    resp = await client.get(ALGOLIA_URL, params=params)
                    resp.raise_for_status()
                    data = resp.json()

                    for hit in data.get("hits", []):
                        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
                        if url in seen_urls:
                            continue
                        seen_urls.add(url)

                        title = hit.get("title", "")
                        author = hit.get("author", "")
                        points = hit.get("points", 0)
                        comments = hit.get("num_comments", 0)
                        created_str = hit.get("created_at", "")
                        created = None
                        if created_str:
                            try:
                                created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                            except (ValueError, TypeError):
                                pass

                        body = title
                        story_text = hit.get("story_text", "")
                        if story_text:
                            body = f"{title}\n\n{story_text}"

                        all_items.append(RawItem(
                            url=url,
                            title=title,
                            body=body,
                            author=author,
                            published_at=created,
                            raw_json=json.dumps({
                                "hn_id": hit.get("objectID"),
                                "points": points,
                                "num_comments": comments,
                                "query": query,
                            }, ensure_ascii=False),
                        ))

            return SyncResult(
                source_key=source_key,
                items=all_items,
                success=True,
                stats={"fetched": len(all_items), "queries": len(queries)},
            )

        except Exception as e:
            logger.exception("HN search adapter sync failed")
            return SyncResult(
                source_key=source_key, items=[], success=False, error=str(e),
            )
