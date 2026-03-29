"""Tests for the GitHub org releases adapter."""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prism.sources.github_releases import GithubReleasesAdapter, _is_recent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _recent_ts() -> str:
    """ISO timestamp 1 hour ago."""
    return (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _old_ts() -> str:
    """ISO timestamp 72 hours ago."""
    return (datetime.now(timezone.utc) - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_repos_response(org: str, repo_names: list[str]) -> list[dict]:
    return [
        {"name": name, "owner": {"login": org}}
        for name in repo_names
    ]


def _make_releases_response(repo_name: str, published_at: str) -> list[dict]:
    return [
        {
            "html_url": f"https://github.com/testorg/{repo_name}/releases/tag/v1.0",
            "name": "v1.0.0",
            "tag_name": "v1.0",
            "body": f"Release notes for {repo_name}",
            "published_at": published_at,
            "author": {"login": "testuser"},
        }
    ]


def _make_mock_response(data, status_code: int = 200):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json = MagicMock(return_value=data)
    mock_resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=mock_resp
        )
    return mock_resp


# ---------------------------------------------------------------------------
# Unit tests for _is_recent
# ---------------------------------------------------------------------------

def test_is_recent_within_48h():
    ts = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _is_recent(ts) is True


def test_is_recent_older_than_48h():
    ts = (datetime.now(timezone.utc) - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _is_recent(ts) is False


def test_is_recent_exactly_at_boundary():
    ts = (datetime.now(timezone.utc) - timedelta(hours=47, minutes=59)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _is_recent(ts) is True


def test_is_recent_invalid_timestamp():
    assert _is_recent("not-a-date") is False
    assert _is_recent("") is False


# ---------------------------------------------------------------------------
# Integration tests for GithubReleasesAdapter.sync (mocked HTTP)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_adapter_sync_recent_release():
    """Recent release should be included in results."""
    repos_data = _make_repos_response("testorg", ["vllm"])
    releases_data = _make_releases_response("vllm", _recent_ts())

    responses = [
        _make_mock_response(repos_data),
        _make_mock_response(releases_data),
    ]

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(side_effect=responses)

        adapter = GithubReleasesAdapter()
        result = await adapter.sync({
            "key": "github:releases",
            "orgs": ["testorg"],
        })

    assert result.success is True
    assert len(result.items) == 1
    assert result.items[0].title == "vllm: v1.0.0"
    assert result.items[0].author == "testuser"
    assert result.items[0].url.startswith("https://github.com")


@pytest.mark.asyncio
async def test_adapter_sync_old_release_filtered():
    """Releases older than 48h should be filtered out."""
    repos_data = _make_repos_response("testorg", ["old-repo"])
    releases_data = _make_releases_response("old-repo", _old_ts())

    responses = [
        _make_mock_response(repos_data),
        _make_mock_response(releases_data),
    ]

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(side_effect=responses)

        adapter = GithubReleasesAdapter()
        result = await adapter.sync({"key": "github:releases", "orgs": ["testorg"]})

    assert result.success is True
    assert result.items == []


@pytest.mark.asyncio
async def test_adapter_sync_no_releases():
    """Repos with no releases should be skipped."""
    repos_data = _make_repos_response("testorg", ["no-release-repo"])
    releases_data = []  # empty

    responses = [
        _make_mock_response(repos_data),
        _make_mock_response(releases_data),
    ]

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(side_effect=responses)

        adapter = GithubReleasesAdapter()
        result = await adapter.sync({"key": "github:releases", "orgs": ["testorg"]})

    assert result.success is True
    assert result.items == []


@pytest.mark.asyncio
async def test_adapter_sync_404_repo():
    """404 on releases endpoint should be silently skipped."""
    repos_data = _make_repos_response("testorg", ["private-repo"])
    not_found = _make_mock_response([], status_code=404)
    not_found.raise_for_status = MagicMock()  # 404 should NOT raise

    responses = [
        _make_mock_response(repos_data),
        not_found,
    ]

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(side_effect=responses)

        adapter = GithubReleasesAdapter()
        result = await adapter.sync({"key": "github:releases", "orgs": ["testorg"]})

    assert result.success is True
    assert result.items == []


@pytest.mark.asyncio
async def test_adapter_sync_http_error():
    """Network error should return SyncResult with success=False."""
    with patch("httpx.AsyncClient") as MockClient:
        import httpx
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(side_effect=httpx.RequestError("network error"))

        adapter = GithubReleasesAdapter()
        result = await adapter.sync({"key": "github:releases", "orgs": ["testorg"]})

    assert result.success is False
    assert result.error != ""
    assert result.items == []


@pytest.mark.asyncio
async def test_adapter_sync_multiple_orgs():
    """Should fetch releases from multiple orgs."""
    repos_org1 = _make_repos_response("org1", ["repo-a"])
    releases_org1 = _make_releases_response("repo-a", _recent_ts())
    repos_org2 = _make_repos_response("org2", ["repo-b"])
    releases_org2 = _make_releases_response("repo-b", _recent_ts())

    responses = [
        _make_mock_response(repos_org1),
        _make_mock_response(releases_org1),
        _make_mock_response(repos_org2),
        _make_mock_response(releases_org2),
    ]

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(side_effect=responses)

        adapter = GithubReleasesAdapter()
        result = await adapter.sync({
            "key": "github:releases",
            "orgs": ["org1", "org2"],
        })

    assert result.success is True
    assert len(result.items) == 2


@pytest.mark.asyncio
async def test_adapter_sync_raw_json_fields():
    """raw_json should contain org, repo, tag, published_at."""
    repos_data = _make_repos_response("myorg", ["myrepo"])
    ts = _recent_ts()
    releases_data = _make_releases_response("myrepo", ts)

    responses = [
        _make_mock_response(repos_data),
        _make_mock_response(releases_data),
    ]

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(side_effect=responses)

        adapter = GithubReleasesAdapter()
        result = await adapter.sync({"key": "github:releases", "orgs": ["myorg"]})

    meta = json.loads(result.items[0].raw_json)
    assert meta["org"] == "myorg"
    assert meta["repo"] == "myrepo"
    assert meta["tag"] == "v1.0"
    assert meta["published_at"] == ts
