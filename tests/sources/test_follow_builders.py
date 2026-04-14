import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from prism.sources.follow_builders import FollowBuildersAdapter

FIXTURE = Path(__file__).parent.parent / "fixtures" / "follow_builders_feed.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def adapter():
    return FollowBuildersAdapter()


def _mock_response(feed_data: dict):
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.json.return_value = feed_data
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_sync_parses_feed(adapter):
    feed = _load_fixture()
    with patch("prism.sources.follow_builders.httpx.AsyncClient") as mock_client:
        ctx = AsyncMock()
        ctx.get.return_value = _mock_response(feed)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=ctx)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await adapter.sync({"source_key": "feed:follow-builders"})

    assert result.success
    assert result.source_key == "feed:follow-builders"
    assert len(result.items) == 3  # 1 from karpathy + 2 from swyx


@pytest.mark.asyncio
async def test_sync_item_fields(adapter):
    feed = _load_fixture()
    with patch("prism.sources.follow_builders.httpx.AsyncClient") as mock_client:
        ctx = AsyncMock()
        ctx.get.return_value = _mock_response(feed)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=ctx)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await adapter.sync({"source_key": "feed:follow-builders"})

    first = result.items[0]
    assert first.url == "https://x.com/karpathy/status/2036487306585268612"
    assert first.author == "karpathy"
    assert first.title.startswith("@karpathy:")
    assert "litellm" in first.body
    assert first.published_at is not None
    assert first.published_at.tzinfo is not None

    raw = json.loads(first.raw_json)
    assert raw["builder_name"] == "Andrej Karpathy"
    assert raw["tweet"]["likes"] == 5432


@pytest.mark.asyncio
async def test_sync_empty_feed(adapter):
    feed = {"generatedAt": "2026-03-25T07:00:00Z", "x": [], "stats": {}}
    with patch("prism.sources.follow_builders.httpx.AsyncClient") as mock_client:
        ctx = AsyncMock()
        ctx.get.return_value = _mock_response(feed)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=ctx)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await adapter.sync({"source_key": "feed:follow-builders"})

    assert result.success
    assert len(result.items) == 0


@pytest.mark.asyncio
async def test_sync_fetch_failure(adapter):
    with patch("prism.sources.follow_builders.httpx.AsyncClient") as mock_client:
        ctx = AsyncMock()
        ctx.get.side_effect = Exception("connection refused")
        mock_client.return_value.__aenter__ = AsyncMock(return_value=ctx)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await adapter.sync({"source_key": "feed:follow-builders"})

    assert not result.success
    assert "connection refused" in result.error


@pytest.mark.asyncio
async def test_sync_skips_tweets_without_url(adapter):
    feed = {
        "generatedAt": "2026-03-25T07:00:00Z",
        "x": [{
            "source": "x", "name": "Test", "handle": "test", "bio": "",
            "tweets": [
                {"id": "1", "text": "has url", "url": "https://x.com/test/status/1",
                 "createdAt": "2026-03-25T00:00:00Z", "likes": 0, "retweets": 0,
                 "replies": 0, "isQuote": False, "quotedTweetId": None},
                {"id": "2", "text": "no url", "url": "",
                 "createdAt": "2026-03-25T00:00:00Z", "likes": 0, "retweets": 0,
                 "replies": 0, "isQuote": False, "quotedTweetId": None},
            ]
        }],
        "stats": {},
    }
    with patch("prism.sources.follow_builders.httpx.AsyncClient") as mock_client:
        ctx = AsyncMock()
        ctx.get.return_value = _mock_response(feed)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=ctx)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await adapter.sync({"source_key": "feed:follow-builders"})

    assert len(result.items) == 1


@pytest.mark.asyncio
async def test_sync_stats(adapter):
    feed = _load_fixture()
    with patch("prism.sources.follow_builders.httpx.AsyncClient") as mock_client:
        ctx = AsyncMock()
        ctx.get.return_value = _mock_response(feed)
        mock_client.return_value.__aenter__ = AsyncMock(return_value=ctx)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await adapter.sync({"source_key": "feed:follow-builders"})

    assert result.stats["builders"] == 2
    assert result.stats["tweets"] == 3
