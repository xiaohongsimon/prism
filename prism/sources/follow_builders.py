"""Follow Builders feed adapter — consumes feed-x.json from GitHub.

Source: https://github.com/zarazhangrui/follow-builders
Feed updated daily via GitHub Actions with X API v2 official token.
"""

import json
import logging
from datetime import datetime, timezone, timedelta

import httpx

from prism.models import RawItem
from prism.sources.base import SyncResult

logger = logging.getLogger(__name__)

DEFAULT_FEED_URL = (
    "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-x.json"
)
STALE_THRESHOLD_HOURS = 48


class FollowBuildersAdapter:
    async def sync(self, config: dict) -> SyncResult:
        source_key = config.get("source_key", "feed:follow-builders")
        feed_url = config.get("url", DEFAULT_FEED_URL)

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(feed_url)
                resp.raise_for_status()
                feed = resp.json()
        except Exception as exc:
            return SyncResult(source_key=source_key, items=[], success=False,
                              error=f"Failed to fetch feed: {exc}")

        # Check staleness
        generated_at = feed.get("generatedAt", "")
        if generated_at:
            try:
                gen_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                age = datetime.now(timezone.utc) - gen_dt
                if age > timedelta(hours=STALE_THRESHOLD_HOURS):
                    logger.warning("Feed is %.1f hours old (threshold=%d)",
                                   age.total_seconds() / 3600, STALE_THRESHOLD_HOURS)
            except (ValueError, TypeError):
                pass

        builders = feed.get("x", [])
        items: list[RawItem] = []

        for builder in builders:
            handle = builder.get("handle", "")
            name = builder.get("name", handle)
            bio = builder.get("bio", "")

            for tweet in builder.get("tweets", []):
                text = tweet.get("text", "")
                tweet_url = tweet.get("url", "")
                if not tweet_url:
                    continue

                # Parse published_at
                published_at = None
                created_at_str = tweet.get("createdAt", "")
                if created_at_str:
                    try:
                        published_at = datetime.fromisoformat(
                            created_at_str.replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        pass

                # Build title: @handle + truncated text for topic_label generation
                title_text = text.replace("\n", " ")[:80]
                title = f"@{handle}: {title_text}"

                raw_json = json.dumps({
                    "tweet": tweet,
                    "builder_name": name,
                    "builder_bio": bio,
                    "feed_generated_at": generated_at,
                }, ensure_ascii=False)

                items.append(RawItem(
                    url=tweet_url,
                    title=title,
                    body=text,
                    author=handle,
                    published_at=published_at,
                    raw_json=raw_json,
                ))

        stats = {
            "builders": len(builders),
            "tweets": len(items),
            "feed_generated_at": generated_at,
        }
        logger.info("FollowBuilders: %d builders, %d tweets", len(builders), len(items))

        return SyncResult(source_key=source_key, items=items, success=True, stats=stats)
