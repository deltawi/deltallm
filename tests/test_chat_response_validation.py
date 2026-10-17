from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.route_group_config import ResponseValidationSettings
from src.router.runtime_generation import require_routing_runtime_generation


def _completion(model: str, message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1700000000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", **message},
                "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _add_fallback(test_app):  # noqa: ANN001, ANN202
    registry_store = test_app.state.router.deployment_registry
    registry = list(registry_store["gpt-4o-mini"])
    primary = registry[0]
    primary.deltallm_params["api_key"] = "provider-key"
    fallback = type(primary)(
        deployment_id="gpt-4o-mini-quality-fallback",
        model_name="gpt-4o-mini",
        deltallm_params={"model": "openai/gpt-4o-mini", "api_key": "provider-key-fallback"},
        model_info={},
    )
    registry.append(fallback)
    registry_store.replace({**registry_store.snapshot(), "gpt-4o-mini": registry})

    async def choose_primary(model_group, request_context):  # noqa: ANN001, ANN202
        del model_group, request_context
        return primary

    test_app.state.router.select_deployment = choose_primary
    return primary, fallback


def _set_validation(test_app, settings: ResponseValidationSettings) -> None:  # noqa: ANN001
    generation = require_routing_runtime_generation(test_app.state)
    generation.app_config.router_settings.response_validation = settings


def _headers(test_app) -> dict[str, str]:  # noqa: ANN001
    return {"Authorization": f"Bearer {test_app.state._test_key}"}


@pytest.mark.asyncio
async def test_empty_success_falls_back_without_cooling_primary(client, test_app):
    primary, fallback = _add_fallback(test_app)
    attempted: list[str | None] = []

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN202
        del timeout
        attempted.append(headers.get("Authorization"))
        content = "" if headers.get("Authorization") == "Bearer provider-key" else "ok"
        return httpx.Response(
            200,
            json=_completion(json["model"], {"content": content}),
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(test_app),
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "ok"
    assert response.headers["x-deltallm-route-deployment"] == fallback.deployment_id
    assert response.headers["x-deltallm-route-fallback-used"] == "true"
    assert "x-deltallm-response-validation" not in response.headers
    assert attempted == ["Bearer provider-key", "Bearer provider-key-fallback"]
    health = await test_app.state.router_state_backend.get_health(primary.deployment_id)
    assert int(health.get("consecutive_failures", 0) or 0) == 0
    assert not await test_app.state.router_state_backend.is_cooled_down(primary.deployment_id)


@pytest.mark.asyncio
async def test_unknown_tool_name_falls_back(client, test_app):
    _, fallback = _add_fallback(test_app)

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN202
        del timeout
        name = "drop_tables" if headers.get("Authorization") == "Bearer provider-key" else "lookup"
        call = {"id": "call_1", "type": "function", "function": {"name": name, "arguments": "{}"}}
        return httpx.Response(
            200,
            json=_completion(json["model"], {"content": None, "tool_calls": [call]}),
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(test_app),
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
        },
    )

    assert response.status_code == 200
    tool_call = response.json()["choices"][0]["message"]["tool_calls"][0]
    assert tool_call["function"]["name"] == "lookup"
    assert response.headers["x-deltallm-route-deployment"] == fallback.deployment_id


@pytest.mark.asyncio
async def test_rejected_answer_is_served_when_no_attempt_remains(client, test_app):
    calls = {"count": 0}

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN202
        del headers, timeout
        calls["count"] += 1
        return httpx.Response(
            200,
            json=_completion(json["model"], {"content": "not json"}),
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = post
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "response_format": {"type": "json_object"},
    }
    response = await client.post("/v1/chat/completions", headers=_headers(test_app), json=body)

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "not json"
    assert response.headers["x-deltallm-response-validation"] == "invalid_json_output"
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_on_exhausted_error_returns_503(client, test_app):
    _set_validation(test_app, ResponseValidationSettings(on_exhausted="error"))

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN202
        del headers, timeout
        return httpx.Response(
            200,
            json=_completion(json["model"], {"content": ""}),
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(test_app),
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "invalid_provider_response"


@pytest.mark.asyncio
async def test_disabled_validation_returns_answer_unchanged(client, test_app):
    _set_validation(test_app, ResponseValidationSettings(enabled=False))

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN202
        del headers, timeout
        return httpx.Response(
            200,
            json=_completion(json["model"], {"content": ""}),
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(test_app),
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == ""
    assert "x-deltallm-response-validation" not in response.headers
