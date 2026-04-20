"""X/Twitter source adapter using bird CLI (private GraphQL via cookie auth).

Replaces the legacy syndication.twitter.com path which X has been rate-limiting
to the point of unusability (persistent 429s as of 2026-04-20).

Cookies are sourced from env (`AUTH_TOKEN`, `CT0`); see
`~/.config/prism/x_cookies.env.example`. daily.sh / hourly.sh are responsible
for loading them before invoking `prism sync`.

Failure modes (all return SyncResult(success=False) — never raise):
- bird CLI not installed
- cookie missing / expired
- subprocess timeout
- bird returned non-JSON or unexpected shape
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from email.utils import parsedate_to_datetime
from typing import Optional

from prism.models import RawItem
from prism.sources.base import SyncResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# bird subprocess wrapper
# ---------------------------------------------------------------------------


async def run_bird_user_tweets(
    handle: str,
    *,
    count: int = 30,
    timeout_s: int = 60,
) -> tuple[Optional[list[dict]], str]:
    """Call `bird user-tweets <handle> --json -n N`.

    Returns (parsed_json_list_or_None, error_message). Never raises.
    """
    if not shutil.which("bird"):
        return None, "bird CLI not installed (npm i -g @leavingme/bird)"

    cmd = [
        "bird",
        "user-tweets",
        handle,
        "--json",
        "--plain",
        "--no-color",
        "-n", str(count),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:  # noqa: BLE001
        return None, f"bird subprocess spawn failed: {e}"

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        return None, f"bird timed out after {timeout_s}s"

    if proc.returncode != 0:
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        low = err.lower()
        if "auth_token" in low or "credentials" in low or "ct0" in low:
            return None, "credentials missing or expired (refresh AUTH_TOKEN/CT0)"
        # Truncate noisy stderr
        head = err.splitlines()[:6]
        return None, f"bird exited {proc.returncode}: {' | '.join(head)[:300]}"

    text = (stdout or b"").decode("utf-8", errors="replace").strip()
    if not text:
        return [], ""

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"bird returned non-JSON: {e}"

    # bird returns either a bare list (single page) or a paged dict
    # `{tweets: [...], nextCursor: ...}` once `-n` exceeds one page.
    if isinstance(data, dict):
        for key in ("tweets", "users", "data", "items"):
            if isinstance(data.get(key), list):
                return data[key], ""
        return None, f"bird JSON has no tweets array (keys: {list(data)[:5]})"
    if isinstance(data, list):
        return data, ""
    return None, f"bird returned unexpected JSON type: {type(data).__name__}"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_bird_tweets(tweets: list[dict], handle: str) -> list[RawItem]:
    """Convert bird's JSON tweet list into RawItem rows.

    Skips retweets (text starts with "RT @" or `retweetedStatus` present).
    Quoted-tweet URLs go into raw_json.quote_urls (compat with downstream
    expand-links / cluster behavior that previously used syndication output).
    """
    items: list[RawItem] = []
    for t in tweets:
        if not isinstance(t, dict):
            continue
        text = t.get("text", "") or ""
        if text.startswith("RT @"):
            continue
        if t.get("retweetedStatus"):
            continue

        tid = str(t.get("id") or "").strip()
        if not tid:
            continue

        author_obj = t.get("author") or {}
        screen_name = (author_obj.get("username") or handle).strip()

        published_at = None
        created_str = t.get("createdAt", "")
        if created_str:
            try:
                published_at = parsedate_to_datetime(created_str)
            except (ValueError, TypeError):
                pass

        # Quote URL — mirror syndication adapter's raw_json shape
        quote_urls: list[str] = []
        qt = t.get("quotedTweet")
        if isinstance(qt, dict):
            qt_id = str(qt.get("id") or "").strip()
            qt_author = ((qt.get("author") or {}).get("username") or "").strip()
            if qt_id and qt_author:
                quote_urls.append(f"https://x.com/{qt_author}/status/{qt_id}")

        raw_data = {"tweet": t, "quote_urls": quote_urls}

        items.append(
            RawItem(
                url=f"https://x.com/{screen_name}/status/{tid}",
                title="",
                body=text,
                author=screen_name,
                published_at=published_at,
                raw_json=json.dumps(raw_data, ensure_ascii=False),
            )
        )
    return items


def detect_threads(tweets: list[dict]) -> list[list[str]]:
    """Group self-reply chains (same author replying to own tweet).

    Works on bird's schema (id, inReplyToStatusId, author.username).
    Returns a list of thread chains, each being a list of tweet id strings
    in chronological (root → tip) order.
    """
    tweet_map: dict[str, dict] = {}
    for t in tweets:
        tid = str(t.get("id") or "")
        if tid:
            tweet_map[tid] = t

    children: dict[str, list[str]] = {}
    is_child: set[str] = set()

    for t in tweets:
        tid = str(t.get("id") or "")
        if not tid:
            continue
        reply_to = str(t.get("inReplyToStatusId") or "")
        author = (t.get("author") or {}).get("username", "")

        if reply_to and reply_to in tweet_map:
            parent_author = (tweet_map[reply_to].get("author") or {}).get("username", "")
            if author == parent_author:
                children.setdefault(reply_to, []).append(tid)
                is_child.add(tid)

    # A thread root is a tweet that has children but is not itself someone's child.
    thread_roots = [rid for rid in children if rid not in is_child]
    threads: list[list[str]] = []
    for root in thread_roots:
        chain = [root]
        current = root
        while current in children:
            next_id = children[current][0]
            chain.append(next_id)
            current = next_id
        threads.append(chain)
    return threads


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class XAdapter:
    """Source adapter for X/Twitter via `bird user-tweets`."""

    async def sync(self, config: dict) -> SyncResult:
        """Fetch and parse tweets for a given handle.

        Config keys:
            handle (str): X handle (required)
            depth (str): "tweet" or "thread" (default "tweet")
            count (int): how many tweets to pull (default 30)
        """
        handle = (config.get("handle") or "").strip()
        source_key = config.get("source_key", f"x:{handle}")
        if not handle:
            return SyncResult(
                source_key=source_key,
                items=[],
                success=False,
                error="missing 'handle' in config",
            )

        depth = config.get("depth", "tweet")
        count = int(config.get("count", 30))

        raw_tweets, err = await run_bird_user_tweets(handle, count=count)
        if raw_tweets is None:
            return SyncResult(
                source_key=source_key,
                items=[],
                success=False,
                error=err,
            )

        items = parse_bird_tweets(raw_tweets, handle)

        stats = {"thread_detected": 0, "thread_partial": 0}
        if depth == "thread":
            threads = detect_threads(raw_tweets)
            stats["thread_detected"] = len(threads)
            ids_in_thread: set[str] = set()
            for chain in threads:
                if len(chain) > 1:
                    ids_in_thread.update(chain)
            for item in items:
                tid = item.url.rsplit("/", 1)[-1]
                if tid in ids_in_thread:
                    item.thread_partial = True
                    stats["thread_partial"] += 1

        return SyncResult(
            source_key=source_key,
            items=items,
            success=True,
            stats=stats,
        )
