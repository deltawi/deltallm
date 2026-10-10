from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpcore2
import httpx
import pytest

from src.providers.http_transport import UpstreamHTTPTransport
from src.upstream_http import build_upstream_http_client


class RecordingBackend(httpcore2.AsyncMockBackend):
    def __init__(self, *, gate: asyncio.Event | None = None, response: bytes | None = None):
        super().__init__([])
        self.gate = gate
        self.response = response or b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"
        self.streams = []
        self.addresses = []
        self.started = asyncio.Event()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        owner = self

        class Stream(httpcore2.AsyncMockStream):
            closed = False
            writes = []

            async def read(self, max_bytes, timeout=None):
                owner.started.set()
                if owner.gate is not None:
                    await owner.gate.wait()
                # Yield so bursts exercise idle reservation across pool passes.
                await asyncio.sleep(0)
                return await super().read(max_bytes, timeout)

            async def write(self, buffer, timeout=None):
                self.writes.append(buffer)
                await asyncio.sleep(0)

            async def aclose(self):
                self.closed = True
                await super().aclose()

        stream = Stream([self.response] * 100)
        stream.writes = []
        self.streams.append(stream)
        self.addresses.append((host, port))
        return stream


def transport_with_backend(backend, *, connections=1):
    transport = UpstreamHTTPTransport(
        limits=httpx.Limits(max_connections=connections, max_keepalive_connections=connections)
    )
    # Test-only injection retains the actual released pool and connection state.
    transport._pool._network_backend = backend
    return transport


async def test_reuses_idle_connections_without_duplicate_assignment(monkeypatch):
    backend = RecordingBackend()
    transport = transport_with_backend(backend, connections=2)
    passes = 0
    assign = transport._pool._assign_requests_to_connections

    def counted_assign():
        nonlocal passes
        passes += 1
        return assign()

    monkeypatch.setattr(transport._pool, "_assign_requests_to_connections", counted_assign)
    async with httpx.AsyncClient(transport=transport) as client:
        # Warm the connection first; then queue a burst behind two connections.
        assert (await client.get("http://provider.example/warm")).status_code == 200
        results = await asyncio.gather(
            *(client.get(f"http://provider.example/{i}") for i in range(40))
        )
        assert all(response.json() == {} for response in results)
        assert len(backend.streams) == 2
        assert passes == 2 * 41
        assert len(transport._pool.connections) == 2
        assert not transport._pool._requests
    assert not transport._pool.connections
    assert all(stream.closed for stream in backend.streams)


async def test_stream_close_releases_pool_and_pool_timeout_keeps_request():
    backend = RecordingBackend()
    transport = transport_with_backend(backend)
    async with httpx.AsyncClient(transport=transport) as client:
        async with client.stream("GET", "http://provider.example/held") as response:
            with pytest.raises(httpx.PoolTimeout) as caught:
                await client.get(
                    "http://provider.example/queued", timeout=httpx.Timeout(1, pool=0.01)
                )
            assert caught.value.request.url.path == "/queued"
            assert len(backend.streams) == 1
            assert len(transport._pool._requests) == 1
            assert not response.is_closed
        assert not transport._pool._requests
        assert (await client.get("http://provider.example/recovered")).status_code == 200
        assert len(backend.streams) == 2  # Abandoned bodies cannot be reused.


async def test_keepalive_limit_counts_idle_not_active_connections():
    backend = RecordingBackend()
    transport = UpstreamHTTPTransport(
        limits=httpx.Limits(max_connections=2, max_keepalive_connections=1)
    )
    transport._pool._network_backend = backend
    async with httpx.AsyncClient(transport=transport) as client:
        first = await client.send(
            client.build_request("GET", "http://provider.example/a"), stream=True
        )
        second = await client.send(
            client.build_request("GET", "http://provider.example/b"), stream=True
        )
        await first.aread()
        await first.aclose()
        assert len(transport._pool.connections) == 2
        assert sum(connection.is_idle() for connection in transport._pool.connections) == 1
        assert not any(stream.closed for stream in backend.streams)
        assert (await client.get("http://provider.example/reuse")).status_code == 200
        assert len(backend.streams) == 2
        await second.aread()
        await second.aclose()
        assert len(transport._pool.connections) == 1
        assert sum(stream.closed for stream in backend.streams) == 1


@pytest.mark.parametrize("cancel_active", [False, True])
async def test_cancellation_releases_waiter_or_active_connection(cancel_active):
    gate = asyncio.Event()
    backend = RecordingBackend(gate=gate)
    transport = transport_with_backend(backend)
    async with httpx.AsyncClient(transport=transport) as client:
        active = asyncio.create_task(client.get("http://provider.example/active"))
        await asyncio.wait_for(backend.started.wait(), 1)
        queued = asyncio.create_task(client.get("http://provider.example/queued"))
        for _ in range(100):
            if len(transport._pool._requests) == 2:
                break
            await asyncio.sleep(0)
        assert len(transport._pool._requests) == 2
        cancelled = active if cancel_active else queued
        survivor = queued if cancel_active else active
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        gate.set()
        assert (await asyncio.wait_for(survivor, 1)).status_code == 200
        assert not transport._pool._requests
        assert (await client.get("http://provider.example/recovered")).status_code == 200
    assert all(stream.closed for stream in backend.streams)


@pytest.mark.parametrize(
    "name",
    [
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "TimeoutException",
        "ConnectError",
        "ReadError",
        "WriteError",
        "NetworkError",
        "ProxyError",
        "UnsupportedProtocol",
        "LocalProtocolError",
        "RemoteProtocolError",
        "ProtocolError",
    ],
)
async def test_keeps_specific_httpx_failure_contract(name, monkeypatch):
    transport = transport_with_backend(RecordingBackend())

    async def fail(request):
        raise getattr(httpcore2, name)("transport failed")

    monkeypatch.setattr(transport._pool, "handle_async_request", fail)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(getattr(httpx, name), match="transport failed") as caught:
            await client.get("http://provider.example/failure")
        assert type(caught.value) is getattr(httpx, name)
        assert caught.value.request.url.path == "/failure"
        assert isinstance(caught.value.__cause__, getattr(httpcore2, name))


async def test_stream_protocol_failure_keeps_httpx_error_and_releases_pool():
    backend = RecordingBackend(response=b"HTTP/1.1 200 OK\r\nContent-Length: 99999\r\n\r\n{}")
    transport = transport_with_backend(backend)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.RemoteProtocolError) as caught:
            await client.get("http://provider.example/truncated")
        assert caught.value.request.url.path == "/truncated"
        assert not transport._pool._requests
        assert all(stream.closed for stream in backend.streams)


async def test_preserves_proxy_mounts_and_capacity(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://user:password@proxy.example:8080")
    monkeypatch.setenv("NO_PROXY", "direct.example")
    settings = SimpleNamespace(
        upstream_http_max_connections=7, upstream_http_max_keepalive_connections=3
    )
    async with build_upstream_http_client(settings) as client:
        direct = client._transport_for_url(httpx.URL("https://direct.example"))
        proxied = client._transport_for_url(httpx.URL("https://provider.example"))
        assert direct is client._transport
        assert proxied is not direct
        assert isinstance(direct, UpstreamHTTPTransport)
        assert isinstance(proxied, UpstreamHTTPTransport)
        for transport in (direct, proxied):
            assert transport._pool._max_connections == 7
            assert transport._pool._max_keepalive_connections == 3
        backend = RecordingBackend()
        proxied._pool._network_backend = backend
        # A plain HTTP forward proxy exercises wire headers, not just construction.
        response = await proxied.handle_async_request(
            httpx.Request("GET", "http://provider.example/")
        )
        await response.aread()
        await response.aclose()
        assert backend.addresses == [("proxy.example", 8080)]
        assert b"Proxy-Authorization: Basic dXNlcjpwYXNzd29yZA==" in b"".join(
            backend.streams[0].writes
        )


async def test_factory_error_hook_still_bounds_provider_error_body(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")
    body = b"x" * 70000
    backend = RecordingBackend(
        response=b"HTTP/1.1 503 Unavailable\r\nContent-Length: 70000\r\n\r\n" + body
    )
    async with build_upstream_http_client(None) as client:
        client._transport._pool._network_backend = backend
        response = await client.get("http://provider.example/error")
        assert response.status_code == 503
        assert len(response.content) == 65536
        assert response.extensions["deltallm_provider_error_body_truncated"]
        assert not client._transport._pool._requests
