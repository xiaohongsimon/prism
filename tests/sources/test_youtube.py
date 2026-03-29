"""Tests for the YouTube channel Atom RSS adapter."""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prism.sources.youtube import YoutubeAdapter, parse_youtube_feed, _is_recent

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _recent_ts() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _old_ts() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _make_atom_feed(entries: list[dict]) -> str:
    """Build a minimal YouTube Atom feed XML string."""
    entry_xml = ""
    for e in entries:
        entry_xml += f"""
    <entry xmlns:yt="http://www.youtube.com/xml/schemas/2015"
           xmlns:media="http://search.yahoo.com/mrss/">
      <yt:videoId>{e.get('video_id', 'vid123')}</yt:videoId>
      <title>{e.get('title', 'Test Video')}</title>
      <link rel="alternate" href="{e.get('url', 'https://www.youtube.com/watch?v=vid123')}"/>
      <published>{e.get('published', _recent_ts())}</published>
      <author><name>{e.get('author', 'Test Channel')}</name></author>
      <media:group>
        <media:description>{e.get('description', 'Video description here')}</media:description>
      </media:group>
    </entry>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/">
  <title>Test Channel</title>
  {entry_xml}
</feed>"""


_EMPTY_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Empty Channel</title>
</feed>"""


# ---------------------------------------------------------------------------
# Unit tests for _is_recent
# ---------------------------------------------------------------------------

def test_is_recent_within_48h():
    ts = (datetime.now(timezone.utc) - timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    assert _is_recent(ts) is True


def test_is_recent_older_than_48h():
    ts = (datetime.now(timezone.utc) - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    assert _is_recent(ts) is False


def test_is_recent_invalid():
    assert _is_recent("") is False
    assert _is_recent("not-a-date") is False


# ---------------------------------------------------------------------------
# Unit tests for parse_youtube_feed
# ---------------------------------------------------------------------------

def test_parse_youtube_feed_basic():
    feed = _make_atom_feed([
        {"video_id": "abc123", "title": "My AI video", "published": _recent_ts()}
    ])
    items = parse_youtube_feed(feed, channel_id="UCtest123")
    assert len(items) == 1
    assert items[0].title == "My AI video"
    assert items[0].url == "https://www.youtube.com/watch?v=vid123"
    assert items[0].author == "Test Channel"
    assert items[0].body == "Video description here"


def test_parse_youtube_feed_channel_id_in_raw_json():
    feed = _make_atom_feed([{"video_id": "xyz789", "published": _recent_ts()}])
    items = parse_youtube_feed(feed, channel_id="UC_channel_001")
    meta = json.loads(items[0].raw_json)
    assert meta["channel_id"] == "UC_channel_001"
    assert meta["video_id"] == "xyz789"


def test_parse_youtube_feed_filters_old_videos():
    feed = _make_atom_feed([
        {"title": "Recent", "published": _recent_ts()},
        {"title": "Old", "published": _old_ts()},
    ])
    items = parse_youtube_feed(feed, channel_id="UCtest")
    assert len(items) == 1
    assert items[0].title == "Recent"


def test_parse_youtube_feed_empty_feed():
    items = parse_youtube_feed(_EMPTY_FEED, channel_id="UCempty")
    assert items == []


def test_parse_youtube_feed_all_old():
    feed = _make_atom_feed([
        {"title": "Old 1", "published": _old_ts()},
        {"title": "Old 2", "published": _old_ts()},
    ])
    items = parse_youtube_feed(feed, channel_id="UCtest")
    assert items == []


# ---------------------------------------------------------------------------
# Integration tests for YoutubeAdapter.sync (mocked HTTP)
# ---------------------------------------------------------------------------

def _make_mock_response(text: str, status_code: int = 200):
    mock_resp = MagicMock()
    mock_resp.text = text
    mock_resp.status_code = status_code
    mock_resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=mock_resp
        )
    return mock_resp


@pytest.mark.asyncio
async def test_adapter_sync_success():
    feed = _make_atom_feed([
        {"video_id": "v001", "title": "LLM Tutorial", "published": _recent_ts()}
    ])
    mock_resp = _make_mock_response(feed)

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=mock_resp)

        adapter = YoutubeAdapter()
        result = await adapter.sync({
            "key": "youtube:ai-interviews",
            "channels": ["UCbfY1234"],
        })

    assert result.success is True
    assert result.source_key == "youtube:ai-interviews"
    assert len(result.items) == 1
    assert result.items[0].title == "LLM Tutorial"


@pytest.mark.asyncio
async def test_adapter_sync_date_filter():
    """Only recent videos should be returned."""
    feed = _make_atom_feed([
        {"title": "New video", "published": _recent_ts()},
        {"title": "Old video", "published": _old_ts()},
    ])
    mock_resp = _make_mock_response(feed)

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=mock_resp)

        adapter = YoutubeAdapter()
        result = await adapter.sync({"key": "youtube:test", "channels": ["UCtest"]})

    assert result.success is True
    assert len(result.items) == 1
    assert result.items[0].title == "New video"


@pytest.mark.asyncio
async def test_adapter_sync_multiple_channels():
    feed1 = _make_atom_feed([{"title": "Channel 1 video", "published": _recent_ts()}])
    feed2 = _make_atom_feed([{"title": "Channel 2 video", "published": _recent_ts()}])

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(side_effect=[
            _make_mock_response(feed1),
            _make_mock_response(feed2),
        ])

        adapter = YoutubeAdapter()
        result = await adapter.sync({
            "key": "youtube:multi",
            "channels": ["UC001", "UC002"],
        })

    assert result.success is True
    assert len(result.items) == 2


@pytest.mark.asyncio
async def test_adapter_sync_empty_channel():
    mock_resp = _make_mock_response(_EMPTY_FEED)

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=mock_resp)

        adapter = YoutubeAdapter()
        result = await adapter.sync({"key": "youtube:empty", "channels": ["UCempty"]})

    assert result.success is True
    assert result.items == []


@pytest.mark.asyncio
async def test_adapter_sync_http_error():
    with patch("httpx.AsyncClient") as MockClient:
        import httpx
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(side_effect=httpx.RequestError("timeout"))

        adapter = YoutubeAdapter()
        result = await adapter.sync({"key": "youtube:test", "channels": ["UCtest"]})

    assert result.success is False
    assert result.error != ""
    assert result.items == []


@pytest.mark.asyncio
async def test_adapter_default_source_key():
    mock_resp = _make_mock_response(_EMPTY_FEED)

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=mock_resp)

        adapter = YoutubeAdapter()
        result = await adapter.sync({"channels": ["UCtest"]})

    assert result.source_key == "youtube:channels"
