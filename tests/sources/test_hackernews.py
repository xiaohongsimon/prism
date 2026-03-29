"""Tests for the Hacker News /best RSS adapter."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prism.sources.hackernews import HackernewsAdapter, parse_hn_rss

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_HN_RSS_SAMPLE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Hacker News: Best</title>
    <link>https://news.ycombinator.com/best</link>
    <item>
      <title>Show HN: I built a local LLM inference server</title>
      <link>https://example.com/llm-server</link>
      <description><![CDATA[A fast local inference server for LLMs]]></description>
      <pubDate>Sun, 29 Mar 2026 10:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Ask HN: Best practices for RAG pipelines?</title>
      <link>https://news.ycombinator.com/item?id=12345</link>
      <description><![CDATA[Discussion about RAG best practices]]></description>
      <pubDate>Sun, 29 Mar 2026 09:00:00 +0000</pubDate>
    </item>
    <item>
      <title>New paper on transformer efficiency</title>
      <link>https://example.com/transformer-paper</link>
      <description><![CDATA[<p>HTML content here</p>]]></description>
      <pubDate>Sun, 29 Mar 2026 08:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

_EMPTY_RSS = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Hacker News: Best</title>
  </channel>
</rss>
"""


# ---------------------------------------------------------------------------
# Unit tests for parse_hn_rss
# ---------------------------------------------------------------------------

def test_parse_hn_rss_basic():
    items = parse_hn_rss(_HN_RSS_SAMPLE)
    assert len(items) == 3
    assert items[0].title == "Show HN: I built a local LLM inference server"
    assert items[0].url == "https://example.com/llm-server"
    assert items[0].body == "A fast local inference server for LLMs"
    assert items[0].author == ""


def test_parse_hn_rss_strips_html():
    items = parse_hn_rss(_HN_RSS_SAMPLE)
    # Third item has <p> tags in description
    assert "<p>" not in items[2].body
    assert "HTML content here" in items[2].body


def test_parse_hn_rss_max_items():
    items = parse_hn_rss(_HN_RSS_SAMPLE, max_items=2)
    assert len(items) == 2


def test_parse_hn_rss_empty_channel():
    items = parse_hn_rss(_EMPTY_RSS)
    assert items == []


def test_parse_hn_rss_no_channel():
    items = parse_hn_rss("<rss version='2.0'></rss>")
    assert items == []


def test_parse_hn_rss_raw_json_fields():
    items = parse_hn_rss(_HN_RSS_SAMPLE)
    import json
    meta = json.loads(items[0].raw_json)
    assert meta["title"] == items[0].title
    assert meta["link"] == items[0].url


# ---------------------------------------------------------------------------
# Integration tests for HackernewsAdapter.sync (mocked HTTP)
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
    mock_resp = _make_mock_response(_HN_RSS_SAMPLE)

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=mock_resp)

        adapter = HackernewsAdapter()
        result = await adapter.sync({"key": "hn:best", "feed_url": "https://hnrss.org/best", "max_items": 15})

    assert result.success is True
    assert result.source_key == "hn:best"
    assert len(result.items) == 3


@pytest.mark.asyncio
async def test_adapter_sync_max_items():
    mock_resp = _make_mock_response(_HN_RSS_SAMPLE)

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=mock_resp)

        adapter = HackernewsAdapter()
        result = await adapter.sync({"key": "hn:best", "feed_url": "https://hnrss.org/best", "max_items": 2})

    assert result.success is True
    assert len(result.items) == 2


@pytest.mark.asyncio
async def test_adapter_sync_http_error():
    with patch("httpx.AsyncClient") as MockClient:
        import httpx
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(side_effect=httpx.RequestError("connection failed"))

        adapter = HackernewsAdapter()
        result = await adapter.sync({"key": "hn:best"})

    assert result.success is False
    assert result.error != ""
    assert result.items == []


@pytest.mark.asyncio
async def test_adapter_sync_empty_feed():
    mock_resp = _make_mock_response(_EMPTY_RSS)

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=mock_resp)

        adapter = HackernewsAdapter()
        result = await adapter.sync({"key": "hn:best"})

    assert result.success is True
    assert result.items == []


@pytest.mark.asyncio
async def test_adapter_default_config():
    """Adapter should work with minimal config using defaults."""
    mock_resp = _make_mock_response(_HN_RSS_SAMPLE)

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=mock_resp)

        adapter = HackernewsAdapter()
        result = await adapter.sync({})

    assert result.source_key == "hn:best"
    assert result.success is True
