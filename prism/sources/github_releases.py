"""GitHub org releases source adapter.

For each configured org, fetches repos sorted by push activity,
then checks for releases published in the last 48 hours.
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta

import httpx

from prism.models import RawItem
from prism.sources.base import SyncResult

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_LOOKBACK_HOURS = 48


def _build_headers() -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _is_recent(published_at: str, hours: int = _LOOKBACK_HOURS) -> bool:
    """Return True if the ISO-8601 timestamp is within the last N hours."""
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        return dt >= cutoff
    except (ValueError, AttributeError):
        return False


class GithubReleasesAdapter:
    """Source adapter for GitHub org releases (last 48 hours)."""

    async def sync(self, config: dict) -> SyncResult:
        """Fetch recent releases from GitHub orgs.

        Config keys:
            key (str): source key used in SyncResult
            orgs (list[str]): GitHub org names to scan
            lookback_hours (int): how many hours back to look (default: 48)
        """
        source_key = config.get("key", "github:releases")
        orgs: list[str] = config.get("orgs", [])
        lookback_hours = int(config.get("lookback_hours", _LOOKBACK_HOURS))
        headers = _build_headers()

        items: list[RawItem] = []

        try:
            async with httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers=headers,
            ) as client:
                for org in orgs:
                    # Fetch org repos sorted by recent push activity
                    repos_url = f"{_GITHUB_API}/orgs/{org}/repos"
                    try:
                        repos_resp = await client.get(
                            repos_url,
                            params={"sort": "pushed", "per_page": 5},
                        )
                        if repos_resp.status_code in (404, 403):
                            logger.warning("Skipping org %s: %s", org, repos_resp.status_code)
                            continue
                        repos_resp.raise_for_status()
                        repos = repos_resp.json()
                    except httpx.HTTPStatusError:
                        logger.warning("Skipping org %s: HTTP error", org)
                        continue

                    for repo in repos:
                        owner = repo.get("owner", {}).get("login", org)
                        repo_name = repo.get("name", "")
                        if not repo_name:
                            continue

                        releases_url = f"{_GITHUB_API}/repos/{owner}/{repo_name}/releases"
                        rel_resp = await client.get(
                            releases_url,
                            params={"per_page": 1},
                        )
                        if rel_resp.status_code == 404:
                            continue
                        rel_resp.raise_for_status()
                        releases = rel_resp.json()

                        if not releases:
                            continue

                        release = releases[0]
                        published_at = release.get("published_at", "")
                        if not _is_recent(published_at, hours=lookback_hours):
                            continue

                        author_login = (
                            release.get("author") or {}
                        ).get("login", "")

                        items.append(
                            RawItem(
                                url=release.get("html_url", ""),
                                title=f"{repo_name}: {release.get('name', release.get('tag_name', ''))}",
                                body=release.get("body") or "",
                                author=author_login,
                                raw_json=json.dumps(
                                    {
                                        "org": org,
                                        "repo": repo_name,
                                        "tag": release.get("tag_name", ""),
                                        "published_at": published_at,
                                    },
                                    ensure_ascii=False,
                                ),
                            )
                        )

            return SyncResult(
                source_key=source_key,
                items=items,
                success=True,
                stats={"orgs": len(orgs), "releases_found": len(items)},
            )

        except Exception as e:
            logger.exception("GitHub releases adapter sync failed")
            return SyncResult(
                source_key=source_key,
                items=[],
                success=False,
                error=str(e),
            )
