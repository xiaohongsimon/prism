"""Reddit source adapter using public JSON API.

Fetches hot posts from specified subreddits without API key.
"""

import json
import logging
from datetime import datetime, timezone

import httpx

from prism.models import RawItem
from prism.sources.base import SyncResult

logger = logging.getLogger(__name__)


class RedditAdapter:
    """Source adapter for Reddit subreddits."""

    async def sync(self, config: dict) -> SyncResult:
        source_key = config.get("key", "reddit:ai")
        subreddits = config.get("subreddits", [])
        max_per_sub = int(config.get("max_per_sub", 10))

        if not subreddits:
            return SyncResult(source_key=source_key, items=[], success=True)

        seen_urls = set()
        all_items = []

        try:
            async with httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers={"User-Agent": "Prism/1.0 (personal recommendation bot)"},
            ) as client:
                for sub in subreddits:
                    url = f"https://www.reddit.com/r/{sub}/hot.json?limit={max_per_sub}"
                    resp = await client.get(url)
                    resp.raise_for_status()
                    data = resp.json()

                    for child in data.get("data", {}).get("children", []):
                        post = child.get("data", {})
                        if post.get("stickied"):
                            continue

                        post_url = post.get("url", "")
                        permalink = f"https://www.reddit.com{post.get('permalink', '')}"
                        # Use external URL if available, otherwise reddit permalink
                        item_url = post_url if post_url and not post_url.startswith("/r/") else permalink

                        if item_url in seen_urls:
                            continue
                        seen_urls.add(item_url)

                        title = post.get("title", "")
                        selftext = post.get("selftext", "")[:2000]
                        author = post.get("author", "")
                        score = post.get("score", 0)
                        comments = post.get("num_comments", 0)
                        created_utc = post.get("created_utc", 0)

                        published = None
                        if created_utc:
                            published = datetime.fromtimestamp(
                                created_utc, tz=timezone.utc
                            )

                        body = title
                        if selftext:
                            body = f"{title}\n\n{selftext}"

                        all_items.append(RawItem(
                            url=item_url,
                            title=title,
                            body=body,
                            author=author,
                            published_at=published,
                            raw_json=json.dumps({
                                "subreddit": sub,
                                "score": score,
                                "num_comments": comments,
                                "permalink": permalink,
                            }, ensure_ascii=False),
                        ))

            return SyncResult(
                source_key=source_key,
                items=all_items,
                success=True,
                stats={"fetched": len(all_items), "subreddits": len(subreddits)},
            )

        except Exception as e:
            logger.exception("Reddit adapter sync failed")
            return SyncResult(
                source_key=source_key, items=[], success=False, error=str(e),
            )
