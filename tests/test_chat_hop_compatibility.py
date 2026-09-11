"""Run this same deterministic facade contract at the feature base and PR 2 head."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import json

import httpx
import pytest

from src.chat.executor import execute_chat
from src.models.errors import ServiceUnavailableError
from src.models.requests import ChatCompletionRequest
from src.providers.openai import OpenAIAdapter
from src.providers.registry import ProviderErrorMapperRegistry
from src.router.router import Deployment


@pytest.mark.asyncio
@pytest.mark.parametrize("status,record_usage", [(200, True), (200, False), (503, True)])
async def test_answer_facade_preserves_wire_result_phase_and_dependency_counts(
    monkeypatch, status, record_usage
):
    wire, phases = [], []
    response = {
        "id": "fixed",
        "created": 1,
        "model": "small",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
    }

    def handler(request):
        wire.append(request)
        return httpx.Response(
            status, json=response if status == 200 else {"error": {"message": "private"}}
        )

    def observe(**kwargs):
        phases.append((kwargs["phase"], kwargs["outcome"], kwargs["response_kind"]))

    monkeypatch.setattr("src.chat.executor.observe_request_phase", observe)
    backend = SimpleNamespace(increment_usage_counters=AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAIAdapter(client)
        adapters = ProviderErrorMapperRegistry(
            openai=adapter,
            azure_openai=adapter,
            anthropic=adapter,
            gemini=adapter,
            bedrock=adapter,
            compatible_chat={},
        )
        # No DB/Redis service is provided. The facade has exactly one canonical usage write.
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    http_client=client,
                    openai_adapter=adapter,
                    provider_error_mapper_registry=adapters,
                    settings=SimpleNamespace(openai_base_url="https://provider.test/v1"),
                    router_state_backend=backend,
                )
            )
        )
        deployment = Deployment(
            deployment_id="concrete",
            model_name="group",
            deltallm_params={"model": "openai/small", "api_key": "test-key"},
            model_info={
                "default_params": {
                    "seed": 7,
                    "temperature": 2,
                    "stream_options": {"include_usage": True},
                }
            },
        )
        payload = ChatCompletionRequest(
            model="group", messages=[{"role": "user", "content": "hello"}], temperature=0.3
        )
        if status >= 400:
            with pytest.raises(ServiceUnavailableError):
                await execute_chat(request, payload, deployment, record_usage=record_usage)
        else:
            result, latency = await execute_chat(
                request, payload, deployment, record_usage=record_usage
            )
            assert (
                result["usage"] == response["usage"]
                and result["choices"][0]["message"]["content"] == "OK"
            )
            assert latency >= 0
        assert not client.is_closed
    assert len(wire) == 1 and wire[0].headers["authorization"] == "Bearer test-key"
    sent = json.loads(wire[0].content)
    assert sent["model"] == "small" and sent["temperature"] == 0.3 and sent["seed"] == 7
    assert "stream_options" not in sent
    expected = [
        ("upstream_transform", "success", "nonstream"),
        ("upstream_http", "error" if status >= 400 else "success", "nonstream"),
    ]
    if status == 200:
        expected.append(("upstream_transform", "success", "nonstream"))
        if record_usage:
            expected.append(("router_usage", "success", "nonstream"))
            backend.increment_usage_counters.assert_awaited_once_with(
                "concrete", {"rpm": 1, "tpm": 6}
            )
    if status != 200 or not record_usage:
        backend.increment_usage_counters.assert_not_awaited()
    assert phases == expected
