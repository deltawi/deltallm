from __future__ import annotations

import asyncio

import httpx
import pytest

from src.billing.budget import BudgetExceeded
from src.services.limit_counter import LimitCounter, RateLimitCheck
from src.models.errors import RateLimitError, ServiceUnavailableError


class _AlwaysBudgetExceeded:
    async def check_budgets(self, **kwargs):
        del kwargs
        raise BudgetExceeded(entity_type="team", entity_id="t1", spend=20.0, max_budget=10.0)


class _SpendRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log_spend(self, **kwargs):
        self.events.append(kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "kwargs"),
    [
        ("/v1/embeddings", {"json": {"model": "text-embedding-3-small", "input": "hello"}}),
        ("/v1/images/generations", {"json": {"model": "gpt-4o-mini", "prompt": "cat"}}),
        ("/v1/rerank", {"json": {"model": "gpt-4o-mini", "query": "q", "documents": ["a", "b"]}}),
        ("/v1/audio/speech", {"json": {"model": "gpt-4o-mini", "input": "hello", "voice": "alloy"}}),
    ],
)
async def test_budget_enforced_for_non_text_endpoints(client, test_app, path, kwargs):
    test_app.state.budget_service = _AlwaysBudgetExceeded()
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    response = await client.post(path, headers=headers, **kwargs)
    assert response.status_code == 429
    payload = response.json()["error"]
    assert payload["type"] == "budget_exceeded"


@pytest.mark.asyncio
async def test_budget_enforced_for_audio_transcriptions(client, test_app):
    test_app.state.budget_service = _AlwaysBudgetExceeded()
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    files = {"file": ("audio.wav", b"abc", "audio/wav")}
    data = {"model": "gpt-4o-mini", "response_format": "json"}
    response = await client.post("/v1/audio/transcriptions", headers=headers, files=files, data=data)
    assert response.status_code == 429
    payload = response.json()["error"]
    assert payload["type"] == "budget_exceeded"


@pytest.mark.asyncio
async def test_chat_fallback_uses_served_deployment_api_base_in_spend_log(client, test_app):
    test_app.state.spend_tracking_service = _SpendRecorder()

    registry = test_app.state.router.deployment_registry["gpt-4o-mini"]
    registry[0].deltallm_params["api_base"] = "https://primary.example/v1"
    registry[0].deltallm_params["api_key"] = "primary-key"
    fallback = type(registry[0])(
        deployment_id="gpt-4o-mini-fallback",
        model_name="gpt-4o-mini",
        deltallm_params={"model": "openai/gpt-4o-mini", "api_key": "fallback-key", "api_base": "https://fallback.example/v1"},
        model_info={},
    )
    registry.append(fallback)

    async def choose_primary(model_group, request_context):  # noqa: ANN001, ANN201
        del request_context
        return test_app.state.router.deployment_registry[model_group][0]

    test_app.state.router.select_deployment = choose_primary

    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        del timeout
        if url.endswith("/chat/completions") and headers.get("Authorization") == "Bearer primary-key":
            return httpx.Response(503, json={"error": "primary down"}, request=httpx.Request("POST", url))
        if url.endswith("/chat/completions"):
            payload = {
                "id": "chatcmpl-fallback",
                "object": "chat.completion",
                "created": 1700000000,
                "model": json["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"error": "not found"}, request=httpx.Request("POST", url))

    test_app.state.http_client.post = post

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200

    await asyncio.sleep(0.05)
    assert test_app.state.spend_tracking_service.events
    last = test_app.state.spend_tracking_service.events[-1]
    assert (last.get("metadata") or {}).get("api_base") == "https://fallback.example/v1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body", "registry_key"),
    [
        (
            "/v1/chat/completions",
            {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False},
            "gpt-4o-mini",
        ),
        (
            "/v1/embeddings",
            {"model": "text-embedding-3-small", "input": "hello"},
            "text-embedding-3-small",
        ),
    ],
)
async def test_explicit_provider_keeps_spend_logging_intact(client, test_app, path, body, registry_key):
    test_app.state.spend_tracking_service = _SpendRecorder()

    deployment = test_app.state.router.deployment_registry[registry_key][0]
    deployment.deltallm_params["provider"] = "openrouter"
    deployment.deltallm_params["api_base"] = "https://openrouter.ai/api/v1"

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    response = await client.post(path, headers=headers, json=body)
    assert response.status_code == 200

    await asyncio.sleep(0.05)
    assert test_app.state.spend_tracking_service.events
    last = test_app.state.spend_tracking_service.events[-1]
    assert last.get("model") == body["model"]
    assert (last.get("metadata") or {}).get("api_base") == "https://openrouter.ai/api/v1"
    assert "cost" in last


@pytest.mark.asyncio
async def test_limit_counter_fail_open_uses_in_memory_fallback():
    limiter = LimitCounter(redis_client=None, degraded_mode="fail_open")
    checks = [RateLimitCheck(scope="key_rpm", entity_id="k1", limit=1, amount=1)]
    await limiter.check_rate_limits_atomic(checks)
    with pytest.raises(RateLimitError):
        await limiter.check_rate_limits_atomic(checks)


@pytest.mark.asyncio
async def test_limit_counter_fail_closed_blocks_when_backend_unavailable():
    limiter = LimitCounter(redis_client=None, degraded_mode="fail_closed")
    checks = [RateLimitCheck(scope="key_rpm", entity_id="k1", limit=1, amount=1)]
    with pytest.raises(ServiceUnavailableError):
        await limiter.check_rate_limits_atomic(checks)
