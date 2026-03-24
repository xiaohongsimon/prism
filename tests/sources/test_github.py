import json
from pathlib import Path

from prism.sources.github import parse_trending_html, is_ai_relevant

FIXTURE = Path(__file__).parent.parent / "fixtures" / "github_trending_response.html"


def test_parse_trending_html():
    html = FIXTURE.read_text()
    repos = parse_trending_html(html)
    assert len(repos) == 2
    assert all(r.url for r in repos)


def test_parse_trending_fields():
    """Verify parsed fields are correct."""
    html = FIXTURE.read_text()
    repos = parse_trending_html(html)
    vllm = repos[0]
    assert vllm.url == "https://github.com/vllm-project/vllm"
    assert vllm.title == "vllm-project/vllm"
    assert vllm.author == "vllm-project"
    assert "LLM" in vllm.body or "inference" in vllm.body

    raw = json.loads(vllm.raw_json)
    assert raw["language"] == "Python"
    assert raw["stars"] == 32500
    assert raw["stars_today"] == 1250


def test_is_ai_relevant():
    assert is_ai_relevant("A framework for building LLM agents") is True
    assert is_ai_relevant("A CSS animation library for buttons") is False


def test_is_ai_relevant_case_insensitive():
    assert is_ai_relevant("TRANSFORMER-based architecture") is True
    assert is_ai_relevant("deep learning framework") is True


def test_parse_trending_non_ai_repo():
    """The CSS repo should parse but not pass AI relevance filter."""
    html = FIXTURE.read_text()
    repos = parse_trending_html(html)
    css_repo = repos[1]
    assert "animate" in css_repo.title
    assert is_ai_relevant(f"{css_repo.title} {css_repo.body}") is False
