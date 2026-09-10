from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from src.providers.chat_discovery import fetch_chat_models
from src.providers.chat_profiles import CHAT_PROVIDER_PROFILES
from src.providers.discovery_runtime import MAX_DISCOVERY_BYTES, ProviderDiscoveryRuntime
from src.outbound.network_policy import OutboundNetworkPolicy
from src.providers.healthcheck import probe_provider_health
from src.providers.model_discovery import discover_provider_models


async def public_resolver(host: str, port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


def discovery_runtime(transport: httpx.AsyncBaseTransport) -> ProviderDiscoveryRuntime:
    return ProviderDiscoveryRuntime(
        transport=transport,
        policy=OutboundNetworkPolicy(resolver=public_resolver, resolution_timeout_seconds=2),
    )


def model_list(contract):
    if contract["provider"] == "qwen":
        return {
            "output": {
                "total": 1,
                "models": [
                    {"model": contract["model"], "name": "Qwen model", "capabilities": ["TG"]}
                ],
            }
        }
    return {"data": [{"id": contract["model"], "name": "Provider model"}]}


async def test_catalog_and_live_discovery(contract):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == httpx.URL(contract["api_base"]).host
        assert request.extensions["sni_hostname"] == httpx.URL(contract["api_base"]).host
        if contract["provider"] == "qwen":
            assert request.url.path == "/api/v1/models"
            assert request.url.params["page_size"] == "100"
        else:
            assert request.url.path.endswith("/models")
        return httpx.Response(200, json=model_list(contract))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await discover_provider_models(
            client,
            discovery_runtime=discovery_runtime(client._transport),
            provider=contract["provider"],
            api_key="test-key",
            default_openai_base_url="https://unrelated.example/v1",
            mode="chat",
        )
    matches = [item for item in result["data"] if item["id"] == contract["model"]]
    assert len(matches) == 1
    assert matches[0]["supported_modes"] == ["chat"]
    assert matches[0]["known_metadata"] is None
    if contract["provider"] == "zai":
        assert not calls
        assert result["warnings"]
        assert matches[0]["source"] == "catalog"
    else:
        assert len(calls) == 1
        assert not result["warnings"]
        assert matches[0]["source"] == "catalog+provider_api"


@pytest.mark.parametrize("status", [401, 403, 429, 500, 302])
async def test_discovery_failure_preserves_catalog_and_redacts(contract, status):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status,
                json={"error": "private upstream secret"},
                headers={"Location": "https://credentials:secret@private.example"},
            )
        )
    ) as client:
        result = await discover_provider_models(
            client,
            discovery_runtime=discovery_runtime(client._transport),
            provider=contract["provider"],
            api_key="test-key",
            default_openai_base_url="https://unrelated.example/v1",
        )
    assert result["data"]
    assert result["warnings"]
    assert "secret" not in str(result)
    assert "private.example" not in str(result)


async def test_health_probes_and_unknown_health_do_not_generate_inference(contract):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == "GET"
        return httpx.Response(200, json=model_list(contract))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await probe_provider_health(
            client,
            {"provider": contract["provider"], "api_key": "test-key"},
            discovery_runtime=discovery_runtime(client._transport),
            default_openai_base_url="https://unrelated.example/v1",
        )
    if contract["provider"] == "zai":
        assert not result.healthy
        assert not result.affects_deployment_health
        assert not calls
    else:
        assert result.healthy
        assert len(calls) == 1


async def test_qwen_region_override_and_truncation_are_explicit():
    calls = []

    def handle(request):
        calls.append(request)
        assert request.headers["host"] == "workspace.cn-beijing.maas.aliyuncs.com"
        assert request.url.path.startswith("/api/v1/")
        return httpx.Response(
            200,
            json={
                "output": {
                    "total": 101,
                    "models": [
                        {"model": "qwen-custom", "capabilities": ["TG"]},
                        {"model": "image-only", "capabilities": ["IG"]},
                    ],
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await discover_provider_models(
            client,
            discovery_runtime=discovery_runtime(client._transport),
            provider="qwen",
            api_key="test-key",
            api_base="https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            default_openai_base_url="https://unrelated.example/v1",
        )
    assert len(calls) == 1
    assert any(item["id"] == "qwen-custom" for item in result["data"])
    assert all(item["id"] != "image-only" for item in result["data"])
    assert "first page" in result["warnings"][0]


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"[]",
        b'{"data":[{"id":""}]}',
        b'{"data":' + json.dumps([{"id": f"model-{i}"} for i in range(501)]).encode() + b"}",
        b"x" * (MAX_DISCOVERY_BYTES + 1),
    ],
)
async def test_invalid_and_oversized_model_lists_fail_without_body_disclosure(body):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    ) as client:
        with pytest.raises(ValueError):
            await fetch_chat_models(
                discovery_runtime(client._transport),
                profile=CHAT_PROVIDER_PROFILES["deepseek"],
                api_base="https://provider.example",
                api_key="test-key",
                timeout=httpx.Timeout(1),
            )


async def test_discovery_cancellation_closes_response():
    entered = asyncio.Event()
    closed = asyncio.Event()

    class BlockingBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b""

        async def aclose(self):
            closed.set()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=BlockingBody()))
    ) as client:
        task = asyncio.create_task(
            fetch_chat_models(
                discovery_runtime(client._transport),
                profile=CHAT_PROVIDER_PROFILES["deepseek"],
                api_base="https://provider.example",
                api_key="test-key",
                timeout=httpx.Timeout(1),
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
