"""GitHub Trending source adapter.

Fetches the GitHub trending page and parses repo information from HTML.
Filters for AI-relevant repositories using keyword matching.
"""

import json
import logging
import re
from typing import Optional

import httpx

from prism.models import RawItem
from prism.sources.base import SyncResult

logger = logging.getLogger(__name__)

GITHUB_TRENDING_URL = "https://github.com/trending"

# AI/ML relevance keywords (same spirit as arXiv keyword list, adapted for GitHub)
_AI_KEYWORDS = [
    "llm", "large language model", "gpt", "transformer",
    "agent", "multi-agent", "tool-use",
    "inference", "serving", "vllm", "trt-llm", "ollama", "llamacpp",
    "rlhf", "dpo", "alignment",
    "reasoning", "chain-of-thought",
    "multimodal", "vision-language", "vlm",
    "rag", "retrieval", "vector database", "embeddings",
    "moe", "mixture of experts",
    "code generation", "codegen", "copilot",
    "fine-tuning", "finetuning", "lora", "qlora",
    "quantization", "pruning", "distillation",
    "diffusion", "stable diffusion", "text-to-image", "image generation",
    "neural network", "deep learning", "machine learning",
    "nlp", "natural language", "chatbot", "conversational",
    "training", "pretraining", "foundation model",
    "attention", "flash attention",
    "knowledge graph", "knowledge base",
    "computer vision", "object detection", "segmentation",
    "speech", "tts", "asr", "text-to-speech",
    "reinforcement learning", "robotics",
    "autonomous", "self-driving",
    "ai", "artificial intelligence", "ml",
]

_AI_KEYWORD_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(kw) for kw in _AI_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# HTML parsing patterns
_ARTICLE_RE = re.compile(r'<article\s+class="Box-row">(.*?)</article>', re.DOTALL)
_REPO_HREF_RE = re.compile(r'<h2[^>]*>\s*<a[^>]*href="(/[^"]+)"', re.DOTALL)
_DESCRIPTION_RE = re.compile(r'<p\s+class="col-9[^"]*"[^>]*>(.*?)</p>', re.DOTALL)
_LANGUAGE_RE = re.compile(r'<span\s+itemprop="programmingLanguage">(.*?)</span>', re.DOTALL)
_STARS_RE = re.compile(r'href="[^"]+/stargazers"[^>]*>\s*([\d,]+)', re.DOTALL)
_STARS_TODAY_RE = re.compile(r'([\d,]+)\s+stars?\s+(?:today|this\s+week|this\s+month)', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_int(s: str) -> int:
    """Parse an integer string, stripping commas and whitespace."""
    try:
        return int(s.replace(",", "").strip())
    except (ValueError, AttributeError):
        return 0


def parse_trending_html(html: str) -> list[RawItem]:
    """Parse GitHub trending page HTML into a list of RawItem."""
    items: list[RawItem] = []

    for article_match in _ARTICLE_RE.finditer(html):
        article = article_match.group(1)

        # Repo path (e.g., "/vllm-project/vllm")
        href_match = _REPO_HREF_RE.search(article)
        if not href_match:
            continue
        repo_path = href_match.group(1).strip()
        # Derive owner/repo
        parts = repo_path.strip("/").split("/")
        if len(parts) < 2:
            continue
        owner, repo = parts[0], parts[1]

        # Description
        desc_match = _DESCRIPTION_RE.search(article)
        description = desc_match.group(1).strip() if desc_match else ""

        # Language
        lang_match = _LANGUAGE_RE.search(article)
        language = lang_match.group(1).strip() if lang_match else ""

        # Stars
        stars_match = _STARS_RE.search(article)
        stars = _parse_int(stars_match.group(1)) if stars_match else 0

        # Stars today
        stars_today_match = _STARS_TODAY_RE.search(article)
        stars_today = _parse_int(stars_today_match.group(1)) if stars_today_match else 0

        url = f"https://github.com/{owner}/{repo}"

        raw_data = {
            "owner": owner,
            "repo": repo,
            "description": description,
            "language": language,
            "stars": stars,
            "stars_today": stars_today,
        }

        items.append(
            RawItem(
                url=url,
                title=f"{owner}/{repo}",
                body=description,
                author=owner,
                raw_json=json.dumps(raw_data, ensure_ascii=False),
            )
        )

    return items


def is_ai_relevant(text: str) -> bool:
    """Check if text contains AI/ML-related keywords."""
    return bool(_AI_KEYWORD_RE.search(text))


async def fetch_repo_details(owner: str, repo: str) -> dict:
    """Fetch additional repo details (README, issues/PRs).

    STUB: returns empty dict. Will be implemented in a later iteration
    to fetch README (first 500 tokens) and recent issues/PRs via GitHub API.
    """
    return {}


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class GithubAdapter:
    """Source adapter for GitHub Trending page."""

    async def sync(self, config: dict) -> SyncResult:
        """Fetch and parse GitHub trending repos.

        Config keys:
            since (str): "daily", "weekly", or "monthly" (default "daily")
            language (str): programming language filter (default: none)
            ai_filter (bool): only return AI-relevant repos (default True)
        """
        since = config.get("since", "daily")
        language = config.get("language", "")
        ai_filter = config.get("ai_filter", True)
        source_key = config.get("source_key", "github_trending:daily")

        try:
            params: dict[str, str] = {"since": since}
            if language:
                params["language"] = language

            async with httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                resp = await client.get(GITHUB_TRENDING_URL, params=params)
                resp.raise_for_status()
                html = resp.text

            items = parse_trending_html(html)

            # Apply AI relevance filter
            if ai_filter:
                filtered = [item for item in items if is_ai_relevant(f"{item.title} {item.body}")]
            else:
                filtered = items

            return SyncResult(
                source_key=source_key,
                items=filtered,
                success=True,
                stats={
                    "total_repos": len(items),
                    "ai_relevant": len(filtered),
                },
            )

        except Exception as e:
            logger.exception("GitHub trending adapter sync failed")
            return SyncResult(
                source_key=source_key,
                items=[],
                success=False,
                error=str(e),
            )
