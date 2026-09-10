from __future__ import annotations

import asyncio

import httpcore
import httpx
import pytest

from src.outbound.network_policy import OutboundNetworkPolicy
from src.metrics.provider_discovery import DiscoveryOutcome
from src.providers.chat_profiles import CHAT_PROVIDER_PROFILES
from src.providers.discovery_runtime import ProviderDiscoveryRuntime
from src.providers.healthcheck import _probe_chat_profile
from src.upstream_http import (
    build_control_http_client,
    build_control_http_transport,
    build_health_check_request_timeout,
)


class RecordingBackend(httpcore.AsyncMockBackend):
    def __init__(self, *, read_gate: asyncio.Event | None = None):
        super().__init__([])
        self.read_gate = read_gate
        self.connections = []
        self.active = 0
        self.peak = 0

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        owner = self

        class Stream(httpcore.AsyncMockStream):
            hostname = None
            writes = []
            closed = False

            async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
                self.hostname = server_hostname
                return self

            async def read(self, max_bytes, timeout=None):
                if owner.read_gate is not None:
                    await owner.read_gate.wait()
                await asyncio.sleep(0.002)
                return await super().read(max_bytes, timeout)

            async def write(self, buffer, timeout=None):
                self.writes.append(buffer)

            async def aclose(self):
                if not self.closed:
                    self.closed = True
                    owner.active -= 1
                await super().aclose()

        stream = Stream([b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n{"data":[]}'])
        stream.writes = []
        self.connections.append((host, port, stream))
        self.active += 1
        self.peak = max(self.active, self.peak)
        return stream


@pytest.mark.parametrize("overlap", [False, True])
async def test_real_pool_isolates_pinned_origins_and_bounds_queued_work(monkeypatch, overlap):
    monkeypatch.setattr("src.upstream_http.CONTROL_HTTP_MAX_CONNECTIONS", 2)
    backend = RecordingBackend()
    transport = build_control_http_transport()
    # Test-only network injection preserves the production HTTPX/httpcore pool.
    transport._pool._network_backend = backend
    addresses = iter([("93.184.216.34",)] * 4 + [("93.184.216.35",)])

    async def resolve(host, port):
        return next(addresses)

    runtime = ProviderDiscoveryRuntime(
        transport=transport,
        policy=OutboundNetworkPolicy(allowed_ports=[443, 8443], resolver=resolve),
    )
    urls = [
        "https://a.example/models",
        "https://b.example/models",
        "https://a.example/models",
        "https://a.example:8443/models",
        "https://a.example/models",
    ]
    async with build_control_http_client(transport=transport):
        if overlap:
            await asyncio.gather(
                *(runtime.fetch(url, api_key="secret", timeout=httpx.Timeout(1)) for url in urls)
            )
        else:
            for url in urls:
                await runtime.fetch(url, api_key="secret", timeout=httpx.Timeout(1))
        assert transport._pool.connections == []
    assert len(backend.connections) == len(urls)
    assert backend.peak <= 2
    assert backend.active == runtime.gate.active == runtime.gate.waiters == 0
    assert sorted(stream.hostname for _, _, stream in backend.connections) == ["a.example"] * 4 + [
        "b.example"
    ]
    assert all(stream.closed for _, _, stream in backend.connections)
    assert {(host, port) for host, port, _ in backend.connections} == {
        ("93.184.216.34", 443),
        ("93.184.216.34", 8443),
        ("93.184.216.35", 443),
    }


async def test_explicit_transport_preserves_proxy_mounts_but_discovery_bypasses_them(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("NO_PROXY", "direct.example")
    transport = build_control_http_transport()
    backend = RecordingBackend()
    transport._pool._network_backend = backend
    async with build_control_http_client(transport=transport) as client:
        assert client._transport_for_url(httpx.URL("https://direct.example")) is transport
        assert client._transport_for_url(httpx.URL("https://provider.example")) is not transport

        async def resolve(host, port):
            return ("93.184.216.34",)

        runtime = ProviderDiscoveryRuntime(
            transport=transport, policy=OutboundNetworkPolicy(resolver=resolve)
        )
        await runtime.fetch(
            "https://provider.example/models", api_key="secret", timeout=httpx.Timeout(1)
        )
    assert len(backend.connections) == 1
    assert backend.connections[0][0] == "93.184.216.34"


@pytest.mark.parametrize(
    "timeout",
    [
        pytest.param(
            build_health_check_request_timeout(
                None, read_timeout_seconds=10, health_check_timeout_seconds=30
            ),
            id="production-defaults",
        ),
        pytest.param(httpx.Timeout(0.25, pool=2), id="pool-exceeds-total"),
        pytest.param(httpx.Timeout(0.25, pool=None), id="unlimited-pool"),
        pytest.param(httpx.Timeout(0.25, pool=0.01), id="shorter-pool"),
    ],
)
async def test_real_pool_saturation_preserves_health_and_recovers(monkeypatch, timeout):
    monkeypatch.setattr("src.upstream_http.CONTROL_HTTP_MAX_CONNECTIONS", 1)
    monkeypatch.setenv("NO_PROXY", "*")
    backend = RecordingBackend()
    transport = build_control_http_transport()
    transport._pool._network_backend = backend
    runtime = ProviderDiscoveryRuntime(transport=transport, policy=OutboundNetworkPolicy())
    outcomes = []
    monkeypatch.setattr(
        "src.providers.discovery_runtime.record_discovery",
        lambda outcome, elapsed: outcomes.append(outcome),
    )

    async def probe():
        return await _probe_chat_profile(
            runtime,
            CHAT_PROVIDER_PROFILES["deepseek"],
            {"api_key": "secret", "api_base": "https://93.184.216.34"},
            timeout,
        )

    async with build_control_http_client(transport=transport) as client:
        # An ordinary control request owns the only connection until its body closes.
        async with client.stream("GET", "https://93.184.216.34/held"):
            result = await probe()
            assert not result.healthy and not result.affects_deployment_health
            assert result.error == "Provider discovery capacity exceeded"
            assert outcomes == [DiscoveryOutcome.CAPACITY]
            assert len(backend.connections) == 1
            assert len(transport._pool._requests) == 1
            assert runtime.gate.active == runtime.gate.waiters == 0

        recovered = await probe()
        assert recovered.healthy
        assert outcomes == [DiscoveryOutcome.CAPACITY, DiscoveryOutcome.SUCCESS]
        assert transport._pool.connections == transport._pool._requests == []
    assert len(backend.connections) == 2
    assert backend.active == runtime.gate.active == runtime.gate.waiters == 0


async def test_real_pool_provider_read_deadline_still_affects_health(monkeypatch):
    backend = RecordingBackend(read_gate=asyncio.Event())
    transport = build_control_http_transport()
    transport._pool._network_backend = backend
    runtime = ProviderDiscoveryRuntime(transport=transport, policy=OutboundNetworkPolicy())
    outcomes = []
    monkeypatch.setattr(
        "src.providers.discovery_runtime.record_discovery",
        lambda outcome, elapsed: outcomes.append(outcome),
    )
    async with build_control_http_client(transport=transport):
        result = await _probe_chat_profile(
            runtime,
            CHAT_PROVIDER_PROFILES["deepseek"],
            {"api_key": "secret", "api_base": "https://93.184.216.34"},
            httpx.Timeout(0.1),
        )
        assert not result.healthy and result.affects_deployment_health
        assert result.error == "Provider health check timed out"
        assert outcomes == [DiscoveryOutcome.TIMEOUT]
        assert transport._pool.connections == transport._pool._requests == []
    assert len(backend.connections) == 1
    assert backend.active == runtime.gate.active == runtime.gate.waiters == 0
