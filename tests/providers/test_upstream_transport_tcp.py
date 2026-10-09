from __future__ import annotations

import asyncio
from contextlib import suppress
import ssl

import httpx
import pytest

from src.upstream_http import build_upstream_http_client
from tests.providers.test_control_transport import tls_contexts as _control_tls_contexts


tls_contexts = _control_tls_contexts


@pytest.fixture
async def loopback_provider():
    tasks = set()
    connections = []
    release = asyncio.Event()

    async def serve(reader, writer):
        tasks.add(asyncio.current_task())
        connections.append(writer)
        try:
            while True:
                headers = await reader.readuntil(b"\r\n\r\n")
                path = headers.split(b" ")[1]
                if path == b"/slow":
                    await release.wait()
                if path == b"/stream":
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\none\r\n"
                    )
                    await writer.drain()
                    await release.wait()
                    writer.write(b"3\r\ntwo\r\n0\r\n\r\n")
                else:
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            tasks.discard(asyncio.current_task())

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", connections, release
    finally:
        server.close()
        await server.wait_closed()
        release.set()
        for task in tuple(tasks):
            task.cancel()
        await asyncio.gather(*tuple(tasks), return_exceptions=True)


async def test_real_socket_reuse_and_read_timeout_recovery(loopback_provider, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")
    url, connections, _ = loopback_provider
    async with build_upstream_http_client(None) as client:
        for _ in range(3):
            assert (await client.get(url)).json() == {}
        assert len(connections) == 1
        with pytest.raises(httpx.ReadTimeout) as caught:
            await client.get(url + "/slow", timeout=httpx.Timeout(1, read=0.02))
        assert caught.value.request.url.path == "/slow"
        assert (await client.get(url)).json() == {}
        assert len(connections) == 2
        assert not client._transport._pool._requests


async def test_real_stream_cancel_and_early_close_recover(loopback_provider, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")
    url, _, _ = loopback_provider
    async with build_upstream_http_client(None) as client:
        async with client.stream("GET", url + "/stream") as response:
            iterator = response.aiter_bytes()
            assert await anext(iterator) == b"one"
            pending = asyncio.create_task(anext(iterator))
            await asyncio.sleep(0)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            await iterator.aclose()
        assert not client._transport._pool._requests
        assert (await client.get(url)).json() == {}
        async with client.stream("GET", url + "/stream") as response:
            assert await anext(response.aiter_bytes()) == b"one"
        assert not client._transport._pool._requests
        assert (await client.get(url)).json() == {}


async def test_real_tls_uses_ca_environment_and_verifies_hostname(tls_contexts, monkeypatch):
    server_context, cert_path = tls_contexts
    monkeypatch.setenv("SSL_CERT_FILE", cert_path)
    monkeypatch.setenv("NO_PROXY", "*")
    received = []
    tasks = set()

    async def serve(reader, writer):
        tasks.add(asyncio.current_task())
        try:
            received.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
            await writer.drain()
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            tasks.discard(asyncio.current_task())

    server = await asyncio.start_server(serve, "127.0.0.1", 0, ssl=server_context)
    port = server.sockets[0].getsockname()[1]
    try:
        async with build_upstream_http_client(None) as client:
            response = await client.get(
                f"https://127.0.0.1:{port}/", extensions={"sni_hostname": "a.example"}
            )
            assert response.json() == {}
            with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
                await client.get(f"https://127.0.0.1:{port}/")
    finally:
        server.close()
        await server.wait_closed()
        await asyncio.gather(*tuple(tasks))
    assert len(received) == 1


async def test_real_proxy_tunnel_preserves_tls_hostname_and_verification(tls_contexts, monkeypatch):
    server_context, cert_path = tls_contexts
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", cert_path)
    received = []
    tasks = set()

    async def proxy(reader, writer):
        tasks.add(asyncio.current_task())
        try:
            connect = await reader.readuntil(b"\r\n\r\n")
            assert connect.startswith(b"CONNECT provider.example:443 HTTP/1.1\r\n")
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await writer.start_tls(server_context)
            received.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
            await writer.drain()
        except (ConnectionError, ssl.SSLError, asyncio.IncompleteReadError):
            pass  # Expected for the deliberately rejected TLS hostname.
        finally:
            writer.close()
            with suppress(ConnectionError, ssl.SSLError):
                await writer.wait_closed()
            tasks.discard(asyncio.current_task())

    server = await asyncio.start_server(proxy, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{port}")
    try:
        async with build_upstream_http_client(None) as client:
            response = await client.get(
                "https://provider.example/", extensions={"sni_hostname": "a.example"}
            )
            assert response.json() == {}
            with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
                await client.get("https://provider.example/")
    finally:
        server.close()
        await server.wait_closed()
        await asyncio.gather(*tuple(tasks))
    assert len(received) == 1
