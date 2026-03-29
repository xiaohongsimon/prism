"""Tests for the Model Economics (OpenRouter) adapter."""

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prism.sources.model_economics import ModelEconomicsAdapter, _build_summary, _format_price

# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

_SAMPLE_MODELS = [
    {
        "id": "anthropic/claude-3-opus",
        "context_length": 200000,
        "pricing": {"prompt": "0.000015", "completion": "0.000075"},
    },
    {
        "id": "openai/gpt-4o",
        "context_length": 128000,
        "pricing": {"prompt": "0.000005", "completion": "0.000015"},
    },
    {
        "id": "google/gemini-1.5-pro",
        "context_length": 1000000,
        "pricing": {"prompt": "0.0000035", "completion": "0.0000105"},
    },
    {
        "id": "meta-llama/llama-3-70b",
        "context_length": 8192,
        "pricing": {"prompt": "0.00000059", "completion": "0.00000079"},
    },
    {
        "id": "free-model/test",
        "context_length": 4096,
        "pricing": {"prompt": "0", "completion": "0"},
    },
]

_OPENROUTER_RESPONSE = {"data": _SAMPLE_MODELS}


def _make_mock_response(data: dict, status_code: int = 200):
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
# Unit tests for helper functions
# ---------------------------------------------------------------------------

def test_format_price_normal():
    # $0.000015 per token = $15 per 1M tokens
    result = _format_price("0.000015")
    assert result == "$15.0000"


def test_format_price_zero():
    result = _format_price("0")
    assert result == "$0.0000"


def test_format_price_none():
    assert _format_price(None) == "N/A"


def test_format_price_invalid():
    assert _format_price("not-a-number") == "N/A"


def test_build_summary_basic():
    summary = _build_summary(_SAMPLE_MODELS)
    # Should mention top models
    assert "gemini" in summary.lower() or "google" in summary.lower()
    assert "claude" in summary.lower() or "anthropic" in summary.lower()
    # Should include context length
    assert "1,000,000" in summary or "200,000" in summary


def test_build_summary_top_10_limit():
    # Create 15 models
    many_models = [
        {
            "id": f"model/{i}",
            "context_length": i * 1000,
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        }
        for i in range(1, 16)
    ]
    summary = _build_summary(many_models)
    # Should only list 10 models (lines starting with " 1." through "10.")
    assert "10." in summary
    assert "11." not in summary


def test_build_summary_empty():
    summary = _build_summary([])
    assert "No models available" in summary


def test_build_summary_sorts_by_context():
    models = [
        {"id": "small-model", "context_length": 4096, "pricing": {}},
        {"id": "large-model", "context_length": 1000000, "pricing": {}},
    ]
    summary = _build_summary(models)
    # large-model should appear before small-model
    large_pos = summary.find("large-model")
    small_pos = summary.find("small-model")
    assert large_pos < small_pos


# ---------------------------------------------------------------------------
# Integration tests for ModelEconomicsAdapter.sync (mocked HTTP)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_adapter_sync_success():
    mock_resp = _make_mock_response(_OPENROUTER_RESPONSE)

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=mock_resp)

        adapter = ModelEconomicsAdapter()
        result = await adapter.sync({"key": "economics:models"})

    assert result.success is True
    assert result.source_key == "economics:models"
    assert len(result.items) == 1


@pytest.mark.asyncio
async def test_adapter_sync_item_fields():
    """Verify the summary RawItem has the correct structure."""
    mock_resp = _make_mock_response(_OPENROUTER_RESPONSE)

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=mock_resp)

        adapter = ModelEconomicsAdapter()
        result = await adapter.sync({"key": "economics:models"})

    item = result.items[0]
    today = str(date.today())

    assert item.url == f"openrouter:models:{today}"
    assert today in item.title
    assert "Model Economics Snapshot" in item.title
    assert item.author == "openrouter"
    assert len(item.body) > 0


@pytest.mark.asyncio
async def test_adapter_sync_raw_json_contains_models():
    """raw_json should contain the full model list."""
    mock_resp = _make_mock_response(_OPENROUTER_RESPONSE)

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=mock_resp)

        adapter = ModelEconomicsAdapter()
        result = await adapter.sync({"key": "economics:models"})

    meta = json.loads(result.items[0].raw_json)
    assert "models" in meta
    assert len(meta["models"]) == len(_SAMPLE_MODELS)
    assert "fetched_at" in meta


@pytest.mark.asyncio
async def test_adapter_sync_empty_model_list():
    """Empty model list should still return a valid item."""
    mock_resp = _make_mock_response({"data": []})

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=mock_resp)

        adapter = ModelEconomicsAdapter()
        result = await adapter.sync({"key": "economics:models"})

    assert result.success is True
    assert len(result.items) == 1
    assert "No models available" in result.items[0].body


@pytest.mark.asyncio
async def test_adapter_sync_http_error():
    with patch("httpx.AsyncClient") as MockClient:
        import httpx
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(side_effect=httpx.RequestError("connection refused"))

        adapter = ModelEconomicsAdapter()
        result = await adapter.sync({"key": "economics:models"})

    assert result.success is False
    assert result.error != ""
    assert result.items == []


@pytest.mark.asyncio
async def test_adapter_sync_stats():
    mock_resp = _make_mock_response(_OPENROUTER_RESPONSE)

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=mock_resp)

        adapter = ModelEconomicsAdapter()
        result = await adapter.sync({"key": "economics:models"})

    assert result.stats is not None
    assert result.stats["total_models"] == len(_SAMPLE_MODELS)


@pytest.mark.asyncio
async def test_adapter_default_source_key():
    mock_resp = _make_mock_response({"data": []})

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=mock_resp)

        adapter = ModelEconomicsAdapter()
        result = await adapter.sync({})

    assert result.source_key == "economics:models"
