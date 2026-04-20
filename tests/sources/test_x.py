"""Tests for prism.sources.x (bird-backed X adapter).

Bird subprocess is mocked — these tests run offline.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from prism.sources import x as x_mod
from prism.sources.x import (
    XAdapter,
    detect_threads,
    parse_bird_tweets,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "x_bird_user_tweets.json"


# ---------------------------------------------------------------------------
# parse_bird_tweets
# ---------------------------------------------------------------------------


def test_parse_bird_tweets_basic():
    data = json.loads(FIXTURE.read_text())
    items = parse_bird_tweets(data, handle="karpathy")
    assert len(items) > 0
    assert all(item.url.startswith("https://x.com/") for item in items)
    assert all(item.author for item in items)
    assert all(item.body for item in items)


def test_parse_bird_tweets_published_at():
    data = json.loads(FIXTURE.read_text())
    items = parse_bird_tweets(data, handle="karpathy")
    assert items[0].published_at is not None
    # Fixture is from 2026
    assert items[0].published_at.year == 2026


def test_parse_bird_tweets_extracts_quote_urls():
    data = json.loads(FIXTURE.read_text())
    items = parse_bird_tweets(data, handle="karpathy")
    # Find at least one tweet with a quoted tweet (fixture has several)
    found_quote = False
    for item in items:
        raw = json.loads(item.raw_json)
        if raw["quote_urls"]:
            found_quote = True
            assert raw["quote_urls"][0].startswith("https://x.com/")
            break
    assert found_quote, "fixture should contain at least one quoted tweet"


def test_parse_bird_tweets_skips_retweets():
    raw = [
        {"id": "1", "text": "RT @someone: original tweet", "author": {"username": "k"}},
        {"id": "2", "text": "real tweet", "author": {"username": "k"}},
        {"id": "3", "text": "another", "author": {"username": "k"},
         "retweetedStatus": {"id": "999"}},
    ]
    items = parse_bird_tweets(raw, handle="k")
    assert [i.body for i in items] == ["real tweet"]


def test_parse_bird_tweets_skips_malformed():
    raw = [
        {"text": "no id"},                      # missing id
        "not a dict",                           # type: ignore
        {"id": "", "text": "empty id"},         # empty id
        {"id": "1", "text": "ok", "author": {"username": "k"}},
    ]
    items = parse_bird_tweets(raw, handle="k")
    assert len(items) == 1
    assert items[0].body == "ok"


def test_parse_bird_tweets_falls_back_to_handle_when_author_missing():
    raw = [{"id": "1", "text": "anon", "createdAt": ""}]
    items = parse_bird_tweets(raw, handle="fallback_handle")
    assert items[0].author == "fallback_handle"
    assert items[0].url == "https://x.com/fallback_handle/status/1"


# ---------------------------------------------------------------------------
# detect_threads
# ---------------------------------------------------------------------------


def test_detect_threads_self_reply_chain():
    tweets = [
        {"id": "1", "author": {"username": "k"}, "inReplyToStatusId": None},
        {"id": "2", "author": {"username": "k"}, "inReplyToStatusId": "1"},
        {"id": "3", "author": {"username": "k"}, "inReplyToStatusId": "2"},
    ]
    threads = detect_threads(tweets)
    assert len(threads) == 1
    assert threads[0] == ["1", "2", "3"]


def test_detect_threads_skips_replies_to_others():
    tweets = [
        {"id": "1", "author": {"username": "alice"}, "inReplyToStatusId": None},
        {"id": "2", "author": {"username": "bob"}, "inReplyToStatusId": "1"},
    ]
    assert detect_threads(tweets) == []


def test_detect_threads_handles_empty():
    assert detect_threads([]) == []


# ---------------------------------------------------------------------------
# XAdapter.sync — full flow with mocked bird
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_happy_path():
    fake_tweets = [
        {"id": "1", "text": "hello", "author": {"username": "k"}, "createdAt": ""},
        {"id": "2", "text": "world", "author": {"username": "k"}, "createdAt": ""},
    ]

    async def fake_run(handle, count=30, timeout_s=60):
        return fake_tweets, ""

    with patch.object(x_mod, "run_bird_user_tweets", side_effect=fake_run):
        adapter = XAdapter()
        result = await adapter.sync({"handle": "k", "depth": "tweet"})

    assert result.success is True
    assert len(result.items) == 2
    assert result.source_key == "x:k"


@pytest.mark.asyncio
async def test_sync_thread_marks_partial():
    fake_tweets = [
        {"id": "1", "text": "root", "author": {"username": "k"},
         "inReplyToStatusId": None, "createdAt": ""},
        {"id": "2", "text": "reply self", "author": {"username": "k"},
         "inReplyToStatusId": "1", "createdAt": ""},
        {"id": "3", "text": "standalone", "author": {"username": "k"},
         "inReplyToStatusId": None, "createdAt": ""},
    ]

    async def fake_run(handle, count=30, timeout_s=60):
        return fake_tweets, ""

    with patch.object(x_mod, "run_bird_user_tweets", side_effect=fake_run):
        adapter = XAdapter()
        result = await adapter.sync({"handle": "k", "depth": "thread"})

    assert result.success is True
    by_id = {item.url.rsplit("/", 1)[-1]: item for item in result.items}
    assert by_id["1"].thread_partial is True
    assert by_id["2"].thread_partial is True
    assert by_id["3"].thread_partial is False
    assert result.stats["thread_detected"] == 1


@pytest.mark.asyncio
async def test_sync_credential_failure_returns_error():
    async def fake_run(handle, count=30, timeout_s=60):
        return None, "credentials missing or expired (refresh AUTH_TOKEN/CT0)"

    with patch.object(x_mod, "run_bird_user_tweets", side_effect=fake_run):
        adapter = XAdapter()
        result = await adapter.sync({"handle": "k"})

    assert result.success is False
    assert "credentials" in result.error
    assert result.items == []


@pytest.mark.asyncio
async def test_sync_missing_handle():
    adapter = XAdapter()
    result = await adapter.sync({})
    assert result.success is False
    assert "handle" in result.error
