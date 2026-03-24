"""arXiv source adapter using RSS feeds.

Fetches RSS from export.arxiv.org for specified categories,
parses XML (RDF format), and filters by AI/ML keyword whitelist.
"""

import json
import logging
import re
import xml.etree.ElementTree as ET
from typing import Optional

import httpx

from prism.models import RawItem
from prism.sources.base import SyncResult

logger = logging.getLogger(__name__)

ARXIV_RSS_URL = "http://export.arxiv.org/rss/{category}"

# RDF namespaces used by arXiv RSS
_NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rss": "http://purl.org/rss/1.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

# ~40 AI/ML keyword whitelist for filtering papers
_AI_KEYWORDS = [
    "llm", "large language model", "gpt", "transformer",
    "agent", "multi-agent", "tool-use", "tool use",
    "inference", "serving", "vllm", "trt-llm",
    "rlhf", "reinforcement learning from human feedback", "dpo", "ppo",
    "alignment", "safety", "red team", "jailbreak",
    "reasoning", "chain-of-thought", "cot", "tree-of-thought",
    "scaling", "scaling law", "emergent",
    "multimodal", "vision-language", "vlm", "image-text",
    "rag", "retrieval", "retrieval-augmented",
    "moe", "mixture of experts", "sparse",
    "code generation", "code-generation", "codegen",
    "fine-tuning", "finetuning", "lora", "qlora", "adapter",
    "quantization", "pruning", "distillation", "compression",
    "diffusion", "text-to-image", "image generation",
    "embedding", "vector", "representation learning",
    "pretraining", "pre-training", "foundation model",
    "prompt", "in-context learning", "icl", "few-shot",
    "attention", "flash attention", "linear attention",
    "knowledge graph", "knowledge distillation",
    "neural architecture search", "nas",
    "federated learning", "privacy",
    "benchmark", "evaluation", "leaderboard",
]

# Compile a single regex for efficient matching
_AI_KEYWORD_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(kw) for kw in _AI_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def parse_rss(xml_text: str) -> list[RawItem]:
    """Parse arXiv RSS (RDF format) XML into a list of RawItem."""
    root = ET.fromstring(xml_text)
    items: list[RawItem] = []

    for item_el in root.findall("rss:item", _NS):
        title = (item_el.findtext("rss:title", "", _NS) or "").strip()
        link = (item_el.findtext("rss:link", "", _NS) or "").strip()
        description = (item_el.findtext("rss:description", "", _NS) or "").strip()
        creator = (item_el.findtext("dc:creator", "", _NS) or "").strip()

        # Clean up title — arXiv sometimes includes markup like (arXiv:XXXX.XXXXX ...)
        # Keep it as-is for now, just strip whitespace
        title = re.sub(r"\s+", " ", title).strip()

        items.append(
            RawItem(
                url=link,
                title=title,
                body=description,
                author=creator,
                raw_json=json.dumps(
                    {"title": title, "abstract": description, "authors": creator, "link": link},
                    ensure_ascii=False,
                ),
            )
        )
    return items


def keyword_filter(items: list[RawItem]) -> list[RawItem]:
    """Filter items by AI/ML keyword whitelist.

    Matches against title + body (case-insensitive).
    """
    filtered: list[RawItem] = []
    for item in items:
        text = f"{item.title} {item.body}"
        if _AI_KEYWORD_RE.search(text):
            filtered.append(item)
    return filtered


def llm_relevance_filter(items: list[RawItem]) -> list[RawItem]:
    """LLM-based relevance filter — STUB.

    Will be wired in Task 6 to use LLM for deeper relevance scoring.
    Currently returns all items unchanged.
    """
    return items


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class ArxivAdapter:
    """Source adapter for arXiv RSS feeds."""

    async def sync(self, config: dict) -> SyncResult:
        """Fetch and parse arXiv papers for given categories.

        Config keys:
            categories (list[str]): arXiv categories to fetch (default: ["cs.LG", "cs.CL", "cs.AI"])
            filter (str): "keyword" or "llm" (default "keyword")
        """
        categories = config.get("categories", ["cs.LG", "cs.CL", "cs.AI"])
        filter_mode = config.get("filter", "keyword")
        source_key = config.get("source_key", "arxiv:daily")

        all_items: list[RawItem] = []

        try:
            async with httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
            ) as client:
                for category in categories:
                    url = ARXIV_RSS_URL.format(category=category)
                    resp = await client.get(url)
                    resp.raise_for_status()
                    papers = parse_rss(resp.text)
                    all_items.extend(papers)

            # Deduplicate by URL (same paper can appear in multiple categories)
            seen_urls: set[str] = set()
            unique_items: list[RawItem] = []
            for item in all_items:
                if item.url not in seen_urls:
                    seen_urls.add(item.url)
                    unique_items.append(item)

            # Apply filtering
            if filter_mode == "keyword":
                filtered = keyword_filter(unique_items)
            elif filter_mode == "llm":
                filtered = llm_relevance_filter(unique_items)
            else:
                filtered = unique_items

            return SyncResult(
                source_key=source_key,
                items=filtered,
                success=True,
                stats={
                    "total_fetched": len(unique_items),
                    "after_filter": len(filtered),
                    "categories": categories,
                },
            )

        except Exception as e:
            logger.exception("arXiv adapter sync failed")
            return SyncResult(
                source_key=source_key,
                items=[],
                success=False,
                error=str(e),
            )
