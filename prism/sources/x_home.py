"""X home-timeline ("For You") source adapter via bird CLI.

Unlike prism/sources/x.py which pulls tweets from a specific account,
this adapter pulls tweets that X itself recommends to the logged-in user.
The signal is strong: every tweet here is a tweet X chose for *me*
given my on-platform behavior (follows, likes, dwell-time, etc.). Prism
ingests them as raw_items and lets the normal cluster/analyze pipeline
decide which ones are worth surfacing; the `via="x_home"` marker in
raw_json lets ranking upweight them later if we want.

Cookie auth via `AUTH_TOKEN` / `CT0` env vars; daily.sh / hourly.sh
load them from `~/.config/prism/x_cookies.env`. All failures return
SyncResult(success=False) — never raise.
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


async def run_bird_home(
    *,
    count: int = 50,
    timeout_s: int = 60,
) -> tuple[Optional[list[dict]], str]:
    """Call `bird home --json -n N`. Never raises.

    Returns (tweets_or_None, error_message).
    """
    if not shutil.which("bird"):
        return None, "bird CLI not installed (npm i -g @leavingme/bird)"

    cmd = ["bird", "home", "--json", "--plain", "--no-color", "-n", str(count)]
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
        head = err.splitlines()[:6]
        return None, f"bird exited {proc.returncode}: {' | '.join(head)[:300]}"

    text = (stdout or b"").decode("utf-8", errors="replace").strip()
    if not text:
        return [], ""

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"bird returned non-JSON: {e}"

    # bird home returns a bare list (no pagination wrapper).
    if isinstance(data, list):
        return data, ""
    if isinstance(data, dict):
        for key in ("tweets", "items", "data"):
            if isinstance(data.get(key), list):
                return data[key], ""
        return None, f"bird home JSON has no tweets array (keys: {list(data)[:5]})"
    return None, f"bird home returned unexpected JSON type: {type(data).__name__}"


def parse_home_tweets(tweets: list[dict]) -> list[RawItem]:
    """Convert bird home JSON into RawItem rows.

    Schema difference from user-tweets: no `retweetedStatus`/`quotedTweet`
    in the minimal `--json` output. Use `text` starting with "RT @" as
    the only retweet filter here; richer filtering would need `--json-full`.
    """
    items: list[RawItem] = []
    for t in tweets:
        if not isinstance(t, dict):
            continue
        text = (t.get("text") or "").strip()
        if not text or text.startswith("RT @"):
            continue

        tid = str(t.get("id") or "").strip()
        author_obj = t.get("author") or {}
        username = (author_obj.get("username") or "").strip()
        if not tid or not username:
            continue

        published_at = None
        created_str = t.get("createdAt", "")
        if created_str:
            try:
                published_at = parsedate_to_datetime(created_str)
            except (ValueError, TypeError):
                pass

        # Shape raw_json to match what downstream rankers/cards already know
        # how to read (see pairwise.py's tweet-parsing fallback branch).
        raw_data = {
            "tweet": {
                "id": tid,
                "text": text,
                "url": f"https://x.com/{username}/status/{tid}",
                "likes": t.get("likeCount", 0),
                "retweets": t.get("retweetCount", 0),
                "replies": t.get("replyCount", 0),
                "createdAt": created_str,
                "user": {
                    "screen_name": username,
                    "name": author_obj.get("name", "") or username,
                },
            },
            "builder_name": author_obj.get("name", "") or username,
            "via": "x_home",  # marker so ranking can upweight these
        }

        items.append(
            RawItem(
                url=f"https://x.com/{username}/status/{tid}",
                title="",
                body=text,
                author=username,
                published_at=published_at,
                raw_json=json.dumps(raw_data, ensure_ascii=False),
            )
        )
    return items


class XHomeAdapter:
    """Source adapter for X home-timeline (For You) via `bird home`."""

    async def sync(self, config: dict) -> SyncResult:
        source_key = config.get("source_key", "x_home:fyp")
        count = int(config.get("count", 50))

        raw_tweets, err = await run_bird_home(count=count)
        if raw_tweets is None:
            return SyncResult(
                source_key=source_key,
                items=[],
                success=False,
                error=err,
            )

        items = parse_home_tweets(raw_tweets)
        return SyncResult(source_key=source_key, items=items, success=True)
