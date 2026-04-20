"""GitHub "For You" feed adapter via `gh` CLI.

Pulls the authenticated user's received_events — activity from the
people they follow on GitHub. High-signal event types:

  * ReleaseEvent — a new version of a project (rich body)
  * WatchEvent   — a person I follow starred a repo (implicit endorsement)
  * CreateEvent  — a person I follow published a new repo

PushEvent/IssueCommentEvent/ForkEvent are filtered out as too noisy.

WatchEvent rows aggregate multiple starrers for the same repo so the
body reads "starred by a, b, c" instead of producing three near-dupes.

Auth flows through `gh` which reads the user's existing gh auth token
(no extra config). All failures return SyncResult(success=False).
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from collections import OrderedDict
from datetime import datetime
from typing import Optional

from prism.models import RawItem
from prism.sources.base import SyncResult

logger = logging.getLogger(__name__)

_HIGH_SIGNAL_TYPES = {"ReleaseEvent", "WatchEvent", "CreateEvent"}


async def _gh_api(path: str, timeout_s: int = 30) -> tuple[Optional[object], str]:
    """Call `gh api <path>`. Returns (parsed_json_or_None, error)."""
    if not shutil.which("gh"):
        return None, "gh CLI not installed (brew install gh)"

    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", "api", path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:  # noqa: BLE001
        return None, f"gh subprocess spawn failed: {e}"

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        return None, f"gh timed out after {timeout_s}s"

    if proc.returncode != 0:
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        low = err.lower()
        if "authentication" in low or "not logged" in low or "401" in low:
            return None, "gh not authenticated (run `gh auth login`)"
        head = err.splitlines()[:4]
        return None, f"gh exited {proc.returncode}: {' | '.join(head)[:300]}"

    text = (stdout or b"").decode("utf-8", errors="replace").strip()
    if not text:
        return None, "gh returned empty stdout"

    try:
        return json.loads(text), ""
    except json.JSONDecodeError as e:
        return None, f"gh returned non-JSON: {e}"


async def fetch_received_events(
    *,
    username: Optional[str] = None,
    per_page: int = 50,
    timeout_s: int = 30,
) -> tuple[Optional[list[dict]], str]:
    """Return the authenticated user's received_events. Never raises."""
    if not username:
        me, err = await _gh_api("/user", timeout_s=timeout_s)
        if me is None:
            return None, err
        if not isinstance(me, dict) or not me.get("login"):
            return None, "gh api /user returned no login"
        username = me["login"]

    data, err = await _gh_api(
        f"/users/{username}/received_events?per_page={per_page}",
        timeout_s=timeout_s,
    )
    if data is None:
        return None, err
    if not isinstance(data, list):
        return None, f"received_events returned non-list: {type(data).__name__}"
    return data, ""


def _parse_gh_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_events(events: list[dict]) -> list[RawItem]:
    """Turn GitHub events into RawItems, one per repo/release.

    WatchEvent: one row per repo, body lists all starrers.
    ReleaseEvent: one row per release.
    CreateEvent (ref_type=repository): one row per new repo.
    """
    watch_bucket: "OrderedDict[str, dict]" = OrderedDict()
    releases: list[RawItem] = []
    creates: list[RawItem] = []

    for e in events:
        if not isinstance(e, dict):
            continue
        t = e.get("type")
        if t not in _HIGH_SIGNAL_TYPES:
            continue

        repo = (e.get("repo") or {}).get("name") or ""
        actor = (e.get("actor") or {}).get("login") or ""
        created = _parse_gh_dt(e.get("created_at") or "")
        payload = e.get("payload") or {}

        if t == "WatchEvent":
            if not repo:
                continue
            url = f"https://github.com/{repo}"
            entry = watch_bucket.get(repo)
            if entry is None:
                watch_bucket[repo] = {
                    "actors": [actor] if actor else [],
                    "created_at": created,
                    "url": url,
                    "repo": repo,
                }
            else:
                if actor and actor not in entry["actors"]:
                    entry["actors"].append(actor)
                # keep the latest timestamp
                if created and (not entry["created_at"] or created > entry["created_at"]):
                    entry["created_at"] = created

        elif t == "ReleaseEvent":
            release = payload.get("release") or {}
            url = release.get("html_url") or ""
            tag = release.get("tag_name") or ""
            name = release.get("name") or tag
            body = (release.get("body") or "").strip()
            if not url or not repo:
                continue
            title = f"{repo} {name}".strip()
            raw = {
                "event_type": "ReleaseEvent",
                "repo": repo,
                "tag": tag,
                "actor": actor,
                "via": "github_home",
            }
            releases.append(
                RawItem(
                    url=url,
                    title=title,
                    body=body[:4000],
                    author=actor,
                    published_at=created,
                    raw_json=json.dumps(raw, ensure_ascii=False),
                )
            )

        elif t == "CreateEvent":
            if payload.get("ref_type") != "repository":
                continue
            if not repo:
                continue
            url = f"https://github.com/{repo}"
            desc = (payload.get("description") or "").strip()
            title = f"{repo} (new repo)"
            raw = {
                "event_type": "CreateEvent",
                "repo": repo,
                "actor": actor,
                "via": "github_home",
            }
            creates.append(
                RawItem(
                    url=url,
                    title=title,
                    body=desc,
                    author=actor,
                    published_at=created,
                    raw_json=json.dumps(raw, ensure_ascii=False),
                )
            )

    # Emit aggregated WatchEvent rows
    watches: list[RawItem] = []
    for repo, entry in watch_bucket.items():
        actors = entry["actors"]
        n = len(actors)
        if n == 0:
            continue
        # Body describes who starred — ranking signal for "multiple follows like this"
        body = f"Starred by {', '.join(actors)} (from your GitHub follows)."
        title = f"{repo} · starred by {actors[0]}" + (f" +{n-1}" if n > 1 else "")
        raw = {
            "event_type": "WatchEvent",
            "repo": repo,
            "actors": actors,
            "starrer_count": n,
            "via": "github_home",
        }
        watches.append(
            RawItem(
                url=entry["url"],
                title=title,
                body=body,
                author=actors[0],
                published_at=entry["created_at"],
                raw_json=json.dumps(raw, ensure_ascii=False),
            )
        )

    return releases + watches + creates


class GithubHomeAdapter:
    """Source adapter for GitHub received_events (activity of users you follow)."""

    async def sync(self, config: dict) -> SyncResult:
        source_key = config.get("source_key", "github_home:fyp")
        username = config.get("username")
        per_page = int(config.get("count", 50))

        events, err = await fetch_received_events(
            username=username, per_page=per_page
        )
        if events is None:
            return SyncResult(
                source_key=source_key,
                items=[],
                success=False,
                error=err,
            )

        items = parse_events(events)
        return SyncResult(source_key=source_key, items=items, success=True)
