from __future__ import annotations

import json
from copy import deepcopy
from collections.abc import AsyncIterator

import httpx
import pytest

from src.cache.key_builder import CacheKeyBuilder
from src.chat.stream_usage import StreamUsageTracker, estimate_chat_prompt_tokens
from src.models.errors import GatewayCapacityError, InvalidRequestError, ProxyError
from src.models.requests import AssistantChatMessage, ChatCompletionRequest
from src.providers.chat_profiles import CHAT_PROVIDER_PROFILES
from src.providers.profiled_chat import ProfiledChatAdapter
from src.providers.resolution import (
    provider_presets,
    provider_supports_mode,
    resolve_upstream_model,
)


def test_registration_and_prefixes(contract):
    provider = contract["provider"]
    preset = next(item for item in provider_presets() if item["provider"] == provider)
    assert preset["api_base"] == contract["api_base"]
    assert preset["supported_modes"] == ["chat"]
    assert preset["compat"] == "openai"
    assert (
        resolve_upstream_model({"provider": provider, "model": f"{provider}/{contract['model']}"})
        == contract["model"]
    )
    assert (
        resolve_upstream_model({"provider": provider, "model": "another-vendor/custom/model"})
        == "another-vendor/custom/model"
    )
    for mode in ("embedding", "image_generation", "audio_speech", "audio_transcription", "rerank"):
        assert not provider_supports_mode(provider, mode)


async def test_success_and_tool_reasoning_round_trip(contract):
    async with httpx.AsyncClient() as client:
        adapter = ProfiledChatAdapter(client, CHAT_PROVIDER_PROFILES[contract["provider"]])
        for response_kind in ("success", "tool"):
            raw = contract[response_kind]
            original = deepcopy(raw)
            response = await adapter.translate_response(raw, "alias")
            assert raw == original
            result = response.model_dump(mode="json")
            assert result["usage"] == {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 14,
                "prompt_tokens_cached": 6,
            }
            message = result["choices"][0]["message"]
            assert message["content"] == original["choices"][0]["message"]["content"]
            for field in ("reasoning_content", "reasoning_details", "tool_calls"):
                if field in raw["choices"][0]["message"]:
                    assert message[field] == raw["choices"][0]["message"][field]
            request = ChatCompletionRequest.model_validate(
                {
                    "model": "alias",
                    "messages": [{"role": "user", "content": "Weather?"}, message],
                }
            )
            upstream = await adapter.translate_request(
                request,
                {
                    "provider": contract["provider"],
                    "model": contract["model"],
                },
            )
            assert upstream["model"] == contract["model"]
            for field in ("reasoning_content", "reasoning_details"):
                if field in message:
                    assert upstream["messages"][1][field] == message[field]


async def test_request_preserves_nulls_strips_internal_fields_and_uses_provider_defaults(contract):
    async with httpx.AsyncClient() as client:
        adapter = ProfiledChatAdapter(client, CHAT_PROVIDER_PROFILES[contract["provider"]])
        request = ChatCompletionRequest.model_validate(
            {
                "model": "alias",
                "metadata": {"private": "gateway-only"},
                "messages": [
                    {"role": "user", "content": "Weather?"},
                    contract["tool"]["choices"][0]["message"],
                ],
            }
        )
        result = await adapter.translate_request(
            request,
            {
                "provider": contract["provider"],
                "model": contract["model"],
            },
        )
        assert result["messages"][1]["content"] is None
        assert "metadata" not in result
        assert "temperature" not in result
        assert "top_p" not in result
        assert "tool_choice" not in result
        assert "frequency_penalty" not in result
        assert (result.get("reasoning_split") is True) == (contract["provider"] == "minimax")


@pytest.mark.parametrize("field,value", [("n", 2), ("frequency_penalty", 1), ("user", "end-user")])
async def test_unsupported_parameters_fail_explicitly(contract, field, value):
    async with httpx.AsyncClient() as client:
        adapter = ProfiledChatAdapter(client, CHAT_PROVIDER_PROFILES[contract["provider"]])
        request = ChatCompletionRequest.model_validate(
            {
                "model": "alias",
                "messages": [{"role": "user", "content": "Hi"}],
                field: value,
            }
        )
        with pytest.raises(InvalidRequestError, match=field):
            await adapter.translate_request(request, {"model": contract["model"]})


async def test_stream_reasoning_and_cached_usage(contract):
    async def upstream() -> AsyncIterator[str]:
        for line in contract["stream"]:
            yield line

    async with httpx.AsyncClient() as client:
        adapter = ProfiledChatAdapter(client, CHAT_PROVIDER_PROFILES[contract["provider"]])
        result = [line async for line in adapter.translate_stream(upstream())]
    assert result[-1] == "data: [DONE]"
    assert result[1] == contract["stream"][1]
    tracker = StreamUsageTracker()
    for line in result:
        tracker.add_line(line)
    usage = tracker.resolve(
        ChatCompletionRequest(model="alias", messages=[{"role": "user", "content": "Weather?"}])
    )
    assert usage.source == "provider"
    assert usage.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
        "prompt_tokens_cached": 6,
    }


@pytest.mark.parametrize(
    "usage",
    [
        {},
        {"prompt_tokens": -1, "completion_tokens": 4, "total_tokens": 3},
        {"prompt_tokens": True, "completion_tokens": 4, "total_tokens": 5},
        {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 99},
        {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
            "prompt_cache_hit_tokens": 11,
        },
        {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
            "prompt_cache_hit_tokens": 6,
            "prompt_tokens_details": {"cached_tokens": 5},
        },
    ],
)
async def test_invalid_usage_is_sanitized(contract, usage):
    raw = {**contract["success"], "usage": usage}
    async with httpx.AsyncClient() as client:
        adapter = ProfiledChatAdapter(client, CHAT_PROVIDER_PROFILES[contract["provider"]])
        with pytest.raises(ProxyError, match="invalid response"):
            await adapter.translate_success_response(httpx.Response(200, json=raw), "alias")


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
async def test_http_errors_are_sanitized(contract, status):
    async with httpx.AsyncClient() as client:
        adapter = ProfiledChatAdapter(client, CHAT_PROVIDER_PROFILES[contract["provider"]])
        response = httpx.Response(
            status,
            json={"error": {"message": "secret upstream body"}},
            headers={"Retry-After": "3"},
        )
        error = adapter.map_error(
            httpx.HTTPStatusError(
                "secret upstream URL",
                request=httpx.Request("POST", contract["api_base"]),
                response=response,
            )
        )
    assert "secret" not in error.message
    assert "http" not in error.message
    if status == 429:
        assert error.status_code == 429
        assert error.retry_after == 3


async def test_pool_overload_remains_a_gateway_failure(contract):
    async with httpx.AsyncClient() as client:
        adapter = ProfiledChatAdapter(client, CHAT_PROVIDER_PROFILES[contract["provider"]])
        error = adapter.map_error(httpx.PoolTimeout("secret URL"))
    assert isinstance(error, GatewayCapacityError)
    assert not error.affects_deployment_health


@pytest.mark.parametrize("error_type", [httpx.ConnectTimeout, httpx.ReadTimeout])
async def test_timeouts_are_sanitized_provider_failures(contract, error_type):
    async with httpx.AsyncClient() as client:
        adapter = ProfiledChatAdapter(client, CHAT_PROVIDER_PROFILES[contract["provider"]])
        error = adapter.map_error(error_type("private upstream URL"))
    assert error.status_code == 408
    assert "private" not in error.message
    assert error.affects_deployment_health


@pytest.mark.parametrize("frame", ["data: invalid-json", "data: []", "data: [DONE]"])
async def test_malformed_precommit_stream_is_rejected(contract, frame):
    async def upstream() -> AsyncIterator[str]:
        yield frame

    async with httpx.AsyncClient() as client:
        adapter = ProfiledChatAdapter(client, CHAT_PROVIDER_PROFILES[contract["provider"]])
        with pytest.raises(ProxyError):
            _ = [line async for line in adapter.translate_stream(upstream())]


def test_reasoning_is_in_cache_keys_and_prompt_estimates():
    ordinary = ChatCompletionRequest(
        model="alias", messages=[{"role": "assistant", "content": "answer"}]
    )
    thinking = ordinary.model_copy(
        update={
            "messages": [
                AssistantChatMessage(role="assistant", content="answer", reasoning_content="x" * 80)
            ]
        }
    )
    assert CacheKeyBuilder().build_key(ordinary) != CacheKeyBuilder().build_key(thinking)
    assert estimate_chat_prompt_tokens(thinking) > estimate_chat_prompt_tokens(ordinary)
    assert "reasoning_content" not in ordinary.messages[0].model_dump()
    assert "reasoning_details" not in ordinary.messages[0].model_dump()
    assert json.loads(thinking.model_dump_json())["messages"][0]["reasoning_content"] == "x" * 80
