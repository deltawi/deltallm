from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.cache.key_builder import CHAT_EXTRA_PARAMS_KEY_FIELD, CacheKeyBuilder
from src.models.request_serialization import (
    dump_declared_request_fields,
    dump_openai_chat_request,
)
from src.models.requests import ChatCompletionRequest
from src.models.responses import ChatCompletionResponse
from src.providers.chat_profiles import CHAT_PROVIDER_PROFILES
from src.providers.profiled_chat import ProfiledChatAdapter


def _capture_post(test_app, answer: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def post(url, headers, json, timeout, **kwargs):  # noqa: ANN001, ANN202
        del url, headers, timeout, kwargs
        calls.append(dict(json))
        return httpx.Response(
            200,
            json=answer
            or {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1700000000,
                "model": json["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    test_app.state.http_client.post = post
    return calls


def _body(**extra: Any) -> dict[str, Any]:
    return {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}], **extra}


@pytest.mark.asyncio
async def test_undeclared_chat_parameters_reach_openai_and_defaults_are_not_sent(client, test_app):
    calls = _capture_post(test_app)
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json=_body(
            seed=7,
            reasoning_effort="low",
            logprobs=True,
            top_logprobs=2,
            parallel_tool_calls=False,
            chat_template_kwargs={"enable_thinking": False},
        ),
    )

    assert response.status_code == 200, response.text
    sent = calls[-1]
    assert sent["seed"] == 7
    assert sent["reasoning_effort"] == "low"
    assert sent["logprobs"] is True and sent["top_logprobs"] == 2
    assert sent["parallel_tool_calls"] is False
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}
    for default in ("temperature", "top_p", "n", "presence_penalty", "frequency_penalty"):
        assert default not in sent


@pytest.mark.asyncio
async def test_configured_model_defaults_apply_when_the_client_omits_them(client, test_app):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info = {
        **dict(deployment.model_info or {}),
        "default_params": {"temperature": 0.6, "top_p": 0.95},
    }
    calls = _capture_post(test_app)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}

    first = await client.post("/v1/chat/completions", headers=headers, json=_body())
    second = await client.post("/v1/chat/completions", headers=headers, json=_body(temperature=0.2))

    assert first.status_code == 200 and second.status_code == 200
    assert calls[0]["temperature"] == 0.6 and calls[0]["top_p"] == 0.95
    assert calls[1]["temperature"] == 0.2 and calls[1]["top_p"] == 0.95


@pytest.mark.asyncio
async def test_usage_details_and_answer_fields_are_returned(client, test_app):
    _capture_post(
        test_app,
        {
            "id": "chatcmpl-2",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "gpt-4o-mini",
            "service_tier": "default",
            "__native_response__": {"forged": True},
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok", "refusal": None},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_tokens_details": {"cached_tokens": 8},
                "completion_tokens_details": {"reasoning_tokens": 3},
            },
        },
    )
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json=_body(),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["service_tier"] == "default"
    assert data["id"] == "chatcmpl-2" and "__native_response__" not in data
    assert "refusal" in data["choices"][0]["message"]
    assert data["usage"]["prompt_tokens_details"] == {"cached_tokens": 8}
    assert data["usage"]["completion_tokens_details"] == {"reasoning_tokens": 3}


def test_usage_without_details_serializes_as_before_and_ignores_malformed_details():
    plain = ChatCompletionResponse.model_validate(
        {
            "id": "c",
            "created": 1,
            "model": "m",
            "choices": [],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )
    assert plain.model_dump(mode="json")["usage"] == {
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_tokens": 2,
    }
    malformed = ChatCompletionResponse.model_validate(
        {
            "id": "c",
            "created": 1,
            "model": "m",
            "choices": [],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "prompt_tokens_details": [1],
            },
        }
    )
    assert "prompt_tokens_details" not in malformed.model_dump(mode="json")["usage"]


def test_openai_serialization_keeps_tool_discriminators_and_explicit_values():
    request = ChatCompletionRequest.model_validate(
        _body(
            temperature=1.0,
            tools=[{"function": {"name": "f", "parameters": {}}}],
            tool_choice={"function": {"name": "f"}},
        )
    )
    data = dump_openai_chat_request(request)
    assert data["temperature"] == 1.0
    assert data["tools"][0]["type"] == "function"
    assert data["tool_choice"] == {"type": "function", "function": {"name": "f"}}
    assert "top_p" not in data


def test_request_fingerprint_fields_exclude_undeclared_parameters():
    with_extra = ChatCompletionRequest.model_validate(_body(seed=1, reasoning_effort="low"))
    without = ChatCompletionRequest.model_validate(_body())
    assert dump_declared_request_fields(with_extra) == dump_declared_request_fields(without)


@pytest.mark.asyncio
async def test_vendor_profiles_leave_undeclared_parameters_out():
    profile = CHAT_PROVIDER_PROFILES["deepseek"]
    async with httpx.AsyncClient() as http_client:
        adapter = ProfiledChatAdapter(http_client, profile)
        request = ChatCompletionRequest.model_validate(_body(seed=1, reasoning_effort="low"))
        payload = await adapter.translate_request(request, {"model": "deepseek-chat"})
    assert "seed" not in payload and "reasoning_effort" not in payload


def test_cache_key_separates_undeclared_parameters_only_when_present():
    builder = CacheKeyBuilder()
    base = _body()
    assert builder.build_key_from_payload(dict(base)) == builder.build_key_from_payload(
        {**base, CHAT_EXTRA_PARAMS_KEY_FIELD: {}}
    )
    low = builder.build_key_from_payload(
        {**base, CHAT_EXTRA_PARAMS_KEY_FIELD: {"reasoning_effort": "low"}}
    )
    high = builder.build_key_from_payload(
        {**base, CHAT_EXTRA_PARAMS_KEY_FIELD: {"reasoning_effort": "high"}}
    )
    assert low != high
    assert low != builder.build_key_from_payload(dict(base))
