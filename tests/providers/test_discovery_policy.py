from __future__ import annotations

import asyncio
import gzip
from unittest.mock import AsyncMock

import httpx
import pytest

from src.concurrency import BoundedCapacityGate
from src.outbound.network_policy import OutboundNetworkPolicy
from src.providers.chat_discovery import fetch_chat_models
from src.providers.chat_profiles import CHAT_PROVIDER_PROFILES
from src.providers.discovery_runtime import DiscoveryUnavailable, ProviderDiscoveryRuntime
from src.providers.healthcheck import probe_provider_health
from src.providers.model_discovery import discover_provider_models


@pytest.mark.parametrize(
    "url,addresses",
    [
        ("https://169.254.169.254", []),
        ("https://100.100.100.200", []),
        ("https://[::ffff:169.254.169.254]", []),
        ("https://[fd00:ec2::254]", []),
        ("https://10.0.0.1", []),
        ("https://[::1]", []),
        ("https://provider.example", ["93.184.216.34", "10.0.0.1"]),
        ("https://provider.example", ["::ffff:127.0.0.1"]),
        ("https://provider.example:8443", []),
        ("http://provider.example", []),
        ("https://user:secret@provider.example", []),
        ("https://provider.example#secret", []),
        ("https://provider.example?override=", []),
        ("https://provider.example:0", []),
        ("https://provider.example/\npath", []),
        ("https://provider.example/" + "x" * 2048, []),
    ],
)
async def test_denied_discovery_returns_catalog_and_does_not_penalize_health(url, addresses):
    send = AsyncMock(side_effect=AssertionError("denied target dispatched"))
    runtime = ProviderDiscoveryRuntime(
        transport=httpx.MockTransport(send),
        policy=OutboundNetworkPolicy(resolver=AsyncMock(return_value=addresses)),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
        result = await discover_provider_models(
            client,
            provider="deepseek",
            api_key="secret",
            api_base=url,
            default_openai_base_url="https://unused.example",
            discovery_runtime=runtime,
        )
        health = await probe_provider_health(
            client,
            {"provider": "deepseek", "api_key": "secret", "api_base": url},
            default_openai_base_url="https://unused.example",
            discovery_runtime=runtime,
        )
    assert result["data"] and result["warnings"]
    assert not health.healthy and not health.affects_deployment_health
    assert "secret" not in str(result) + str(health)
    send.assert_not_called()


async def test_explicit_private_policy_pins_once_and_never_allows_metadata(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.example")
    resolver = AsyncMock(side_effect=[("10.2.3.4",), ("169.254.169.254",)])
    calls = []

    def handle(request):
        calls.append(request)
        assert str(request.url) == "https://10.2.3.4/models"
        assert request.headers["host"] == "provider.example"
        assert request.headers["authorization"] == "Bearer secret"
        assert request.extensions["sni_hostname"] == "provider.example"
        return httpx.Response(200, json={"data": []})

    runtime = ProviderDiscoveryRuntime(
        transport=httpx.MockTransport(handle),
        policy=OutboundNetworkPolicy(
            allowed_private_cidrs=["0.0.0.0/0", "::/0"], resolver=resolver
        ),
    )
    await runtime.fetch(
        "https://provider.example/models", api_key="secret", timeout=httpx.Timeout(1)
    )
    with pytest.raises(DiscoveryUnavailable):
        await runtime.fetch(
            "https://provider.example/models", api_key="secret", timeout=httpx.Timeout(1)
        )
    assert len(calls) == 1 and resolver.await_count == 2


async def test_discovery_gate_bounds_dns_and_waiters_and_releases_on_cancel():
    entered = asyncio.Event()

    async def resolve(host, port):
        entered.set()
        await asyncio.Event().wait()

    gate = BoundedCapacityGate(concurrency=1, max_waiters=1)
    runtime = ProviderDiscoveryRuntime(
        transport=httpx.MockTransport(AsyncMock()),
        policy=OutboundNetworkPolicy(resolver=resolve),
        gate=gate,
    )

    async def fetch():
        return await runtime.fetch(
            "https://provider.example/models", api_key="secret", timeout=httpx.Timeout(1)
        )

    first = asyncio.create_task(fetch())
    await entered.wait()
    second = asyncio.create_task(fetch())
    await asyncio.sleep(0)
    assert gate.active == gate.waiters == 1
    with pytest.raises(DiscoveryUnavailable, match="capacity"):
        await fetch()
    with pytest.raises(DiscoveryUnavailable, match="capacity"):
        await second
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert gate.active == gate.waiters == 0


@pytest.mark.parametrize("compressed", [False, True])
async def test_discovery_bounds_wire_body_and_decompression(compressed):
    class Body(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            body = b"x" * 2_097_153
            yield gzip.compress(body) if compressed else body

        async def aclose(self):
            self.closed = True

    body = Body()
    runtime = ProviderDiscoveryRuntime(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, stream=body, headers={"content-encoding": "gzip" if compressed else "identity"}
            )
        ),
        policy=OutboundNetworkPolicy(),
    )
    with pytest.raises(ValueError, match="size limit"):
        await fetch_chat_models(
            runtime,
            profile=CHAT_PROVIDER_PROFILES["deepseek"],
            api_base="https://93.184.216.34",
            api_key="secret",
            timeout=httpx.Timeout(1),
        )
    assert body.closed
    assert runtime.gate.active == 0


async def test_success_survives_stalled_close_within_cleanup_grace():
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"data": []}'

        async def aclose(self):
            await asyncio.Event().wait()

    runtime = ProviderDiscoveryRuntime(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Body())),
        policy=OutboundNetworkPolicy(),
    )
    async with asyncio.timeout(0.3):
        body = await runtime.fetch(
            "https://93.184.216.34/models", api_key="secret", timeout=httpx.Timeout(0.05)
        )
    assert body == b'{"data": []}'
    assert runtime.gate.active == 0


async def test_dns_timeout_releases_admission_without_dispatch():
    async def resolve(host, port):
        await asyncio.Event().wait()

    send = AsyncMock(side_effect=AssertionError("DNS timeout dispatched HTTP"))
    runtime = ProviderDiscoveryRuntime(
        transport=httpx.MockTransport(send),
        policy=OutboundNetworkPolicy(resolver=resolve, resolution_timeout_seconds=0.01),
    )
    with pytest.raises(ValueError, match="DNS resolution failed"):
        await runtime.fetch(
            "https://provider.example/models", api_key="secret", timeout=httpx.Timeout(1)
        )
    assert runtime.gate.active == runtime.gate.waiters == 0
    send.assert_not_called()


async def test_pool_capacity_is_local_and_does_not_penalize_health():
    send = AsyncMock(side_effect=httpx.PoolTimeout("private transport material"))
    runtime = ProviderDiscoveryRuntime(
        transport=httpx.MockTransport(send), policy=OutboundNetworkPolicy()
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
        result = await probe_provider_health(
            client,
            {"provider": "deepseek", "api_key": "secret", "api_base": "https://93.184.216.34"},
            default_openai_base_url="https://unused.example",
            discovery_runtime=runtime,
        )
    assert not result.healthy and not result.affects_deployment_health
    assert result.error == "Provider discovery capacity exceeded"
    assert runtime.gate.active == 0
