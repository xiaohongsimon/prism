"""Model economics source adapter using OpenRouter public API.

Fetches model listing from OpenRouter (no auth required),
generates a summary RawItem and stores the full model list as raw_json.
"""

import json
import logging
from datetime import date

import httpx

from prism.models import RawItem
from prism.sources.base import SyncResult

logger = logging.getLogger(__name__)

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def _format_price(price_str: str | None) -> str:
    """Format per-token price as $/1M tokens, or 'N/A'."""
    if price_str is None:
        return "N/A"
    try:
        per_token = float(price_str)
        per_million = per_token * 1_000_000
        return f"${per_million:.4f}"
    except (ValueError, TypeError):
        return "N/A"


def _build_summary(models: list[dict]) -> str:
    """Build a human-readable summary of top models by context length."""
    if not models:
        return "No models available."

    # Sort by context_length descending, take top 10
    sorted_models = sorted(
        models,
        key=lambda m: m.get("context_length", 0) or 0,
        reverse=True,
    )[:10]

    lines = [f"Top 10 models by context length (snapshot {date.today()}):\n"]
    for i, m in enumerate(sorted_models, 1):
        model_id = m.get("id", "unknown")
        ctx = m.get("context_length", 0) or 0
        pricing = m.get("pricing", {}) or {}
        prompt_price = _format_price(pricing.get("prompt"))
        completion_price = _format_price(pricing.get("completion"))
        lines.append(
            f"{i:2}. {model_id}\n"
            f"     Context: {ctx:,} tokens | "
            f"Prompt: {prompt_price}/1M | "
            f"Completion: {completion_price}/1M"
        )

    return "\n".join(lines)


class ModelEconomicsAdapter:
    """Source adapter that snapshots OpenRouter model pricing and context lengths."""

    async def sync(self, config: dict) -> SyncResult:
        """Fetch OpenRouter model list and generate a summary RawItem.

        Config keys:
            key (str): source key used in SyncResult (default: "economics:models")

        Returns a single RawItem per sync with:
            url: "openrouter:models:{date}"
            title: "Model Economics Snapshot {date}"
            body: summary of top 10 models by context length
            raw_json: full model list JSON
        """
        source_key = config.get("key", "economics:models")
        today = date.today()

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(_OPENROUTER_MODELS_URL)
                resp.raise_for_status()
                data = resp.json()

            models: list[dict] = data.get("data", [])
            summary = _build_summary(models)

            item = RawItem(
                url=f"openrouter:models:{today}",
                title=f"Model Economics Snapshot {today}",
                body=summary,
                author="openrouter",
                raw_json=json.dumps(
                    {"fetched_at": str(today), "models": models},
                    ensure_ascii=False,
                ),
            )

            return SyncResult(
                source_key=source_key,
                items=[item],
                success=True,
                stats={"total_models": len(models)},
            )

        except Exception as e:
            logger.exception("Model economics adapter sync failed")
            return SyncResult(
                source_key=source_key,
                items=[],
                success=False,
                error=str(e),
            )
