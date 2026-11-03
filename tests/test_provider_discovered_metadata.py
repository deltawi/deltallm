from __future__ import annotations

import json

import httpx
import pytest

from src.providers.chat_discovery import parse_chat_models
from src.providers.chat_profiles import CHAT_PROVIDER_PROFILES
from src.providers.discovered_metadata import (
    gemini_metadata,
    merge_known_metadata,
    openai_style_metadata,
)


def test_openrouter_limits_and_per_token_prices():
    item = {
        "id": "acme/model-x",
        "context_length": 200000,
        "top_provider": {"max_completion_tokens": 64000},
        "pricing": {
            "prompt": "0.000003",
            "completion": "0.000015",
            "input_cache_read": "0.0000003",
        },
    }
    assert openai_style_metadata(item, provider="openrouter") == {
        "max_tokens": 200000,
        "max_output_tokens": 64000,
        "input_cost_per_token": 0.000003,
        "output_cost_per_token": 0.000015,
        "input_cost_per_token_cache_hit": 0.0000003,
    }


def test_openrouter_variable_price_sentinel_is_ignored():
    item = {"id": "openrouter/auto", "pricing": {"prompt": "-1", "completion": "-1"}}
    assert openai_style_metadata(item, provider="openrouter") == {}


def test_groq_context_window_and_completion_limit():
    item = {
        "id": "llama-3.3-70b-versatile",
        "context_window": 131072,
        "max_completion_tokens": 32768,
    }
    assert openai_style_metadata(item, provider="groq") == {
        "max_tokens": 131072,
        "max_output_tokens": 32768,
    }


def test_together_prices_are_per_million_tokens():
    item = {
        "id": "meta/llama",
        "context_length": 131072,
        "pricing": {"input": 0.88, "output": 0.88},
    }
    metadata = openai_style_metadata(item, provider="together")
    assert metadata["max_tokens"] == 131072
    assert metadata["input_cost_per_token"] == pytest.approx(0.88e-6)
    assert metadata["output_cost_per_token"] == pytest.approx(0.88e-6)


def test_pricing_is_read_only_for_providers_with_a_known_unit():
    item = {"id": "x", "pricing": {"prompt": "0.1", "input": 1}}
    assert openai_style_metadata(item, provider="fireworks") == {}


@pytest.mark.parametrize("value", [0, -5, 1.5, "abc", True, None, float("inf"), 10**12, {"n": 1}])
def test_invalid_limits_are_dropped(value):
    assert openai_style_metadata({"id": "x", "context_length": value}, provider="groq") == {}


def test_absurd_prices_are_dropped():
    item = {"id": "x", "pricing": {"prompt": "5", "completion": "nan"}}
    assert openai_style_metadata(item, provider="openrouter") == {}


def test_gemini_input_and_output_limits():
    assert gemini_metadata({"inputTokenLimit": 1048576, "outputTokenLimit": 65536}) == {
        "max_input_tokens": 1048576,
        "max_output_tokens": 65536,
    }


def test_catalog_values_win_over_provider_values():
    merged = merge_known_metadata(
        {"input_cost_per_token": 0.000002, "max_tokens": 128000},
        {"input_cost_per_token": 0.000009, "max_output_tokens": 16384},
    )
    assert merged == {
        "input_cost_per_token": 0.000002,
        "max_tokens": 128000,
        "max_output_tokens": 16384,
    }
    assert merge_known_metadata(None, {}) is None


def test_mistral_discovery_reads_context_length():
    body = json.dumps(
        {
            "data": [
                {
                    "id": "mistral-large-latest",
                    "capabilities": {"completion_chat": True},
                    "max_context_length": 131072,
                },
                {
                    "id": "mistral-small-latest",
                    "capabilities": {"completion_chat": True},
                    "max_context_length": "unknown",
                },
            ]
        }
    ).encode()
    models = {
        item.id: item for item in parse_chat_models(CHAT_PROVIDER_PROFILES["mistral"], body).models
    }
    assert models["mistral-large-latest"].metadata == {"max_tokens": 131072}
    assert models["mistral-small-latest"].metadata == {}


async def _discover(client, test_app, provider: str, payload: object, **body: str):  # noqa: ANN001, ANN202
    setattr(test_app.state.settings, "master_key", "mk-test")

    async def get(url: str, headers: dict[str, str] | None = None, timeout=None):  # noqa: ANN001, ANN202
        del url, headers, timeout
        return httpx.Response(200, json=payload)

    test_app.state.http_client.get = get
    response = await client.post(
        "/ui/api/provider-models/discover",
        headers={"Authorization": "Bearer mk-test"},
        json={"provider": provider, "mode": "chat", "api_key": "provider-key", **body},
    )
    assert response.status_code == 200
    return {item["id"]: item for item in response.json()["data"]}


@pytest.mark.asyncio
async def test_discovery_returns_provider_limits_for_models_missing_from_catalog(client, test_app):
    models = await _discover(
        client,
        test_app,
        "gemini",
        {
            "models": [
                {
                    "name": "models/gemini-test-model",
                    "inputTokenLimit": 1048576,
                    "outputTokenLimit": 65536,
                },
            ]
        },
    )
    assert models["gemini-test-model"]["known_metadata"] == {
        "max_input_tokens": 1048576,
        "max_output_tokens": 65536,
    }


@pytest.mark.asyncio
async def test_discovery_keeps_catalog_prices_over_provider_values(client, test_app):
    models = await _discover(
        client,
        test_app,
        "groq",
        {
            "data": [
                {"id": "llama-3.3-70b-versatile", "context_window": 1, "max_completion_tokens": 7}
            ]
        },
    )
    metadata = models["llama-3.3-70b-versatile"]["known_metadata"]
    assert metadata["max_tokens"] != 1
    assert metadata["max_output_tokens"] != 7
    assert metadata["input_cost_per_token"] > 0


@pytest.mark.asyncio
async def test_discovery_reads_together_bare_list(client, test_app):
    models = await _discover(
        client,
        test_app,
        "together",
        [
            {
                "id": "meta/llama",
                "context_length": 131072,
                "pricing": {"input": 0.88, "output": 0.88},
            }
        ],
    )
    assert models["meta/llama"]["known_metadata"]["max_tokens"] == 131072
    assert models["meta/llama"]["known_metadata"]["input_cost_per_token"] == pytest.approx(0.88e-6)


def test_anthropic_input_window_and_output_cap():
    from src.providers.discovered_metadata import anthropic_metadata

    assert anthropic_metadata({"max_input_tokens": 1000000, "max_tokens": 128000}) == {
        "max_input_tokens": 1000000,
        "max_output_tokens": 128000,
    }
    assert anthropic_metadata({"max_input_tokens": 0, "max_tokens": None}) == {}


@pytest.mark.asyncio
async def test_discovery_requests_full_anthropic_list_and_reads_limits(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    seen: list[str] = []

    async def get(url: str, headers: dict[str, str] | None = None, timeout=None):  # noqa: ANN001, ANN202
        del headers, timeout
        seen.append(url)
        return httpx.Response(
            200,
            json={
                "data": [{"id": "claude-new", "max_input_tokens": 1000000, "max_tokens": 128000}]
            },
        )

    test_app.state.http_client.get = get
    response = await client.post(
        "/ui/api/provider-models/discover",
        headers={"Authorization": "Bearer mk-test"},
        json={"provider": "anthropic", "mode": "chat", "api_key": "provider-key"},
    )
    assert response.status_code == 200
    assert seen == ["https://api.anthropic.com/v1/models?limit=1000"]
    models = {item["id"]: item for item in response.json()["data"]}
    assert models["claude-new"]["known_metadata"] == {
        "max_input_tokens": 1000000,
        "max_output_tokens": 128000,
    }
