"""X/Twitter source adapter using syndication API.

Fetches timeline from syndication.twitter.com, parses __NEXT_DATA__ JSON,
extracts tweets, detects self-reply threads, and optionally expands them.
"""

import json
import logging
import re
from email.utils import parsedate_to_datetime
from typing import Optional

import httpx

from prism.models import RawItem
from prism.sources.base import SyncResult

logger = logging.getLogger(__name__)

SYNDICATION_URL = "https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}"

# Regex to extract __NEXT_DATA__ JSON from the syndication HTML response
_NEXT_DATA_RE = re.compile(r'(\{"props":\{"pageProps":.*)')

# Pattern to detect quote-tweet URLs in entities
_QUOTE_TWEET_RE = re.compile(r"https?://(?:twitter\.com|x\.com)/\w+/status/(\d+)")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _extract_tweets_from_data(data: dict) -> list[dict]:
    """Navigate the __NEXT_DATA__ structure to extract tweet dicts."""
    try:
        entries = data["props"]["pageProps"]["timeline"]["entries"]
    except (KeyError, TypeError):
        return []
    tweets = []
    for entry in entries:
        try:
            tweet = entry["content"]["tweet"]
            tweets.append(tweet)
        except (KeyError, TypeError):
            continue
    return tweets


def parse_syndication_response(data: dict, handle: str) -> list[RawItem]:
    """Parse the __NEXT_DATA__ JSON into a list of RawItem.

    Skips pure retweets (full_text starts with "RT @").
    """
    tweets = _extract_tweets_from_data(data)
    items: list[RawItem] = []
    for tweet in tweets:
        full_text = tweet.get("full_text", "")
        # Skip retweets
        if full_text.startswith("RT @"):
            continue

        id_str = tweet.get("id_str", "")
        user = tweet.get("user", {})
        screen_name = user.get("screen_name", handle)
        created_at_str = tweet.get("created_at", "")

        # Parse published_at
        published_at = None
        if created_at_str:
            try:
                published_at = parsedate_to_datetime(created_at_str)
            except (ValueError, TypeError):
                pass

        # Extract quote tweet URLs from entities
        quote_urls: list[str] = []
        entities = tweet.get("entities", {})
        for url_obj in entities.get("urls", []):
            expanded = url_obj.get("expanded_url", "")
            if _QUOTE_TWEET_RE.search(expanded):
                quote_urls.append(expanded)

        # Build raw_json with tweet data and any extracted metadata
        raw_data = {
            "tweet": tweet,
            "quote_urls": quote_urls,
        }

        items.append(
            RawItem(
                url=f"https://x.com/{screen_name}/status/{id_str}",
                title="",
                body=full_text,
                author=screen_name,
                published_at=published_at,
                raw_json=json.dumps(raw_data, ensure_ascii=False),
            )
        )
    return items


def detect_threads(tweets: list[dict]) -> list[list[str]]:
    """Group self-reply chains (same author replying to own tweet).

    Returns a list of thread chains, each being a list of tweet id_str
    in chronological order.
    """
    # Build lookup: tweet_id -> tweet
    tweet_map: dict[str, dict] = {}
    for t in tweets:
        tid = t.get("id_str")
        if tid:
            tweet_map[tid] = t

    # Build adjacency: parent_id -> list of child ids (self-replies only)
    children: dict[str, list[str]] = {}
    root_ids: set[str] = set()

    for t in tweets:
        tid = t.get("id_str", "")
        reply_to = t.get("in_reply_to_status_id_str")
        author = t.get("user", {}).get("screen_name", "")

        if reply_to and reply_to in tweet_map:
            parent_author = tweet_map[reply_to].get("user", {}).get("screen_name", "")
            if author == parent_author:
                children.setdefault(reply_to, []).append(tid)
                root_ids.discard(tid)
                if reply_to not in children.get("__child_set__", set()):
                    root_ids.add(reply_to)
                continue
        # Not a self-reply — could be a root
        root_ids.add(tid)

    # Only keep roots that actually have children (i.e., are thread starters)
    thread_roots = [rid for rid in root_ids if rid in children]

    # Walk each chain from root
    threads: list[list[str]] = []
    for root in thread_roots:
        chain = [root]
        current = root
        while current in children:
            # Take first child (threads are linear)
            next_id = children[current][0]
            chain.append(next_id)
            current = next_id
        threads.append(chain)

    return threads


async def _try_expand_thread(tweet_url: str) -> Optional[str]:
    """Attempt to expand a thread using playwright. Returns full thread text or None.

    This is optional in v1 — gracefully degrades if playwright is not installed.
    """
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except ImportError:
        logger.debug("playwright not installed, skipping thread expansion")
        return None

    # Placeholder for actual playwright expansion logic.
    # In a future iteration, this will:
    # 1. Launch headless browser
    # 2. Navigate to the tweet URL
    # 3. Extract full thread text
    logger.debug("Thread expansion not yet implemented, returning None")
    return None


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class XAdapter:
    """Source adapter for X/Twitter via syndication API."""

    async def sync(self, config: dict) -> SyncResult:
        """Fetch and parse tweets for a given handle.

        Config keys:
            handle (str): Twitter handle to fetch (required)
            depth (str): "tweet" or "thread" (default "tweet")
        """
        handle = config.get("handle", "")
        if not handle:
            return SyncResult(
                source_key=f"x:{handle}",
                items=[],
                success=False,
                error="missing 'handle' in config",
            )

        depth = config.get("depth", "tweet")
        source_key = config.get("source_key", f"x:{handle}")

        stats = {"thread_detected": 0, "thread_expanded": 0, "thread_failed": 0}

        try:
            async with httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                url = SYNDICATION_URL.format(handle=handle)
                resp = await client.get(url)
                resp.raise_for_status()
                html = resp.text

            # Extract __NEXT_DATA__ JSON
            match = _NEXT_DATA_RE.search(html)
            if not match:
                return SyncResult(
                    source_key=source_key,
                    items=[],
                    success=False,
                    error="Could not find __NEXT_DATA__ in syndication response",
                )

            # The matched group may have trailing HTML; parse up to valid JSON
            raw_json = match.group(1)
            # Strip trailing </script> etc.
            raw_json = raw_json.split("</script>")[0].strip()
            data = json.loads(raw_json)

            items = parse_syndication_response(data, handle)

            # Thread detection and optional expansion
            if depth == "thread":
                tweets = _extract_tweets_from_data(data)
                threads = detect_threads(tweets)
                stats["thread_detected"] = len(threads)

                for chain in threads:
                    # Try to expand each thread
                    root_url = f"https://x.com/{handle}/status/{chain[0]}"
                    expanded = await _try_expand_thread(root_url)
                    if expanded:
                        stats["thread_expanded"] += 1
                    else:
                        stats["thread_failed"] += 1
                        # Mark items in this thread as partial
                        chain_set = set(chain)
                        for item in items:
                            # Extract id from URL
                            item_id = item.url.rsplit("/", 1)[-1]
                            if item_id in chain_set:
                                item.thread_partial = True

            return SyncResult(
                source_key=source_key,
                items=items,
                success=True,
                stats=stats,
            )

        except Exception as e:
            logger.exception("X adapter sync failed for handle=%s", handle)
            return SyncResult(
                source_key=source_key,
                items=[],
                success=False,
                error=str(e),
            )
