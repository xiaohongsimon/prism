import json
from pathlib import Path

from prism.sources.x import XAdapter, parse_syndication_response, detect_threads

FIXTURE = Path(__file__).parent.parent / "fixtures" / "x_syndication_response.json"


def test_parse_syndication_response():
    data = json.loads(FIXTURE.read_text())
    items = parse_syndication_response(data, handle="karpathy")
    assert len(items) > 0
    assert all(item.url for item in items)
    assert all(item.author == "karpathy" for item in items)


def test_detect_threads():
    tweets = [
        {"id_str": "1", "user": {"screen_name": "karpathy"}, "in_reply_to_status_id_str": None},
        {"id_str": "2", "user": {"screen_name": "karpathy"}, "in_reply_to_status_id_str": "1"},
    ]
    threads = detect_threads(tweets)
    assert len(threads) == 1
    assert threads[0] == ["1", "2"]


def test_retweets_filtered():
    data = json.loads(FIXTURE.read_text())
    items = parse_syndication_response(data, handle="karpathy")
    # The fixture has one RT tweet — it should be filtered
    texts = [item.body for item in items]
    assert not any(t.startswith("RT @") for t in texts)


def test_parse_includes_quote_urls():
    """Verify quote-tweet URLs are extracted into raw_json."""
    data = json.loads(FIXTURE.read_text())
    items = parse_syndication_response(data, handle="karpathy")
    # Second tweet in fixture has a quote URL
    raw = json.loads(items[1].raw_json)
    assert len(raw["quote_urls"]) == 1
    assert "9999" in raw["quote_urls"][0]


def test_detect_threads_no_self_reply():
    """Tweets replying to other users should NOT form threads."""
    tweets = [
        {"id_str": "1", "user": {"screen_name": "alice"}, "in_reply_to_status_id_str": None},
        {"id_str": "2", "user": {"screen_name": "bob"}, "in_reply_to_status_id_str": "1"},
    ]
    threads = detect_threads(tweets)
    assert len(threads) == 0


def test_published_at_parsed():
    data = json.loads(FIXTURE.read_text())
    items = parse_syndication_response(data, handle="karpathy")
    assert items[0].published_at is not None
    assert items[0].published_at.year == 2026
