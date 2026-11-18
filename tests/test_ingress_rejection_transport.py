"""Retryable rejection must preserve its response and the request boundary."""

import asyncio
import json
import socket
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import uvicorn

from src.middleware.ingress import IngressMiddleware, _reject
from tests.test_ingress_admission import runtime, scope

pytestmark = pytest.mark.hermetic


@pytest.mark.parametrize("version", ["1.0", "1.1", "2"])
@pytest.mark.parametrize("status", [400, 408, 413, 503])
async def test_connection_policy_preserves_invalid_body_and_http10_close(version, status):
    receive, send = AsyncMock(), AsyncMock()
    request = scope(runtime(), http_version=version)
    await _reject(request, receive, send, status, "fixture_rejection")
    receive.assert_not_called()
    headers = dict(send.await_args_list[0].args[0]["headers"])
    assert (headers.get(b"connection") == b"close") == (
        version == "1.0" or (version == "1.1" and status != 503)
    )
    assert (headers.get(b"retry-after") == b"1") == (status == 503)


async def read_response(reader):
    header = await reader.readuntil(b"\r\n\r\n")
    lines = header.decode("ascii").split("\r\n")
    headers = dict(line.lower().split(": ", 1) for line in lines[1:] if line)
    body = await reader.readexactly(int(headers["content-length"]))
    return int(lines[0].split()[1]), headers, body


@pytest.mark.parametrize("parser", ["h11", "httptools"])
async def test_rejection_discards_upload_without_new_work_and_idle_socket_expires(parser):
    rt = runtime()
    await rt.requests.acquire(timeout_seconds=1)
    application = SimpleNamespace(state=SimpleNamespace(ingress_runtime=rt))
    requests = []
    started = asyncio.Event()

    async def downstream(scope, receive, send):
        if scope["type"] == "lifespan":
            assert (await receive())["type"] == "lifespan.startup"
            await send({"type": "lifespan.startup.complete"})
            started.set()
            assert (await receive())["type"] == "lifespan.shutdown"
            await send({"type": "lifespan.shutdown.complete"})
            return
        requests.append(scope["path"])
        await send(
            {"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"2")]}
        )
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = IngressMiddleware(downstream)

    async def app(scope, receive, send):
        scope["app"] = application
        await middleware(scope, receive, send)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            http=parser,
            lifespan="on",
            access_log=False,
            timeout_keep_alive=1,
        )
    )
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    writer = None
    try:
        async with asyncio.timeout(5):
            await started.wait()
            while not server.started:
                await asyncio.sleep(0)
            reader, writer = await asyncio.open_connection(*sock.getsockname())
            # Send headers alone. Rejection cannot wait for any body frame.
            writer.write(
                b"POST /v1/chat/completions HTTP/1.1\r\nHost: fixture\r\n"
                b"Content-Length: 262144\r\n\r\n"
            )
            await writer.drain()
            status, headers, body = await read_response(reader)
            assert status == 503 and headers["retry-after"] == "1"
            assert "connection" not in headers
            assert json.loads(body)["error"]["code"] == "gateway_ingress_full"
            assert not requests and rt.buffered_bytes == 0
            assert rt.requests.active == 1 and rt.requests.waiters == 0
            # Bytes that resemble a second request are still part of this upload.
            # The server parser, not application code, owns this boundary.
            fake_request = b"GET /injected HTTP/1.1\r\nHost: fixture\r\n\r\n"
            writer.write(fake_request + b"x" * (262144 - len(fake_request)))
            writer.write(b"GET /health/readiness HTTP/1.1\r\nHost: fixture\r\n\r\n")
            await writer.drain()
            assert (await read_response(reader))[0] == 200
            assert requests == ["/health/readiness"]
            assert rt.buffered_bytes == 0 and rt.requests.active == 1
            # Leave another rejected upload unfinished. No ASGI task or allocation
            # may stay live until its body arrives. The owned idle deadline closes it.
            writer.write(
                b"POST /v1/chat/completions HTTP/1.1\r\nHost: fixture\r\n"
                b"Content-Length: 262144\r\n\r\n"
            )
            await writer.drain()
            assert (await read_response(reader))[0] == 503
            while server.server_state.tasks:
                await asyncio.sleep(0)
            assert await reader.read() == b""
            while server.server_state.connections:
                await asyncio.sleep(0)
            assert rt.requests.waiters == rt.buffered_bytes == 0
            assert requests == ["/health/readiness"]
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        server.should_exit = True
        async with asyncio.timeout(5):
            await serving
        sock.close()
        await rt.requests.release()


@pytest.mark.parametrize("version", ["1.0", "1.1"])
async def test_draining_rejection_closes_the_connection(version):
    receive, send = AsyncMock(), AsyncMock()
    await _reject(scope(runtime(), http_version=version), receive, send, 503, "gateway_draining")
    headers = dict(send.await_args_list[0].args[0]["headers"])
    assert headers.get(b"connection") == b"close"
    assert headers.get(b"retry-after") == b"1"


async def _serve_draining(parser, slots=None):
    application = SimpleNamespace(
        state=SimpleNamespace(process_lifecycle=SimpleNamespace(draining=True))
    )
    requests = []

    async def downstream(scope, receive, send):
        if scope["type"] == "lifespan":
            assert (await receive())["type"] == "lifespan.startup"
            await send({"type": "lifespan.startup.complete"})
            assert (await receive())["type"] == "lifespan.shutdown"
            await send({"type": "lifespan.shutdown.complete"})
            return
        requests.append(scope["path"])

    middleware = IngressMiddleware(downstream)
    if slots is not None:
        middleware.draining_discard.limit = slots

    async def app(scope, receive, send):
        scope["app"] = application
        await middleware(scope, receive, send)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, http=parser, lifespan="on", access_log=False))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        await asyncio.sleep(0)
    return server, serving, sock, requests


async def _stop(server, serving, sock, writer):
    if writer is not None:
        writer.close()
        await asyncio.gather(writer.wait_closed(), return_exceptions=True)
    server.should_exit = True
    async with asyncio.timeout(5):
        await serving
    sock.close()


def _late_body_exchange(address):
    # A blocking socket leaves the response in the kernel buffer, as a client
    # does while it is still sending. A reset then discards the response.
    with socket.create_connection(address, timeout=3) as client:
        client.sendall(
            b"POST /v1/chat/completions HTTP/1.1\r\nHost: fixture\r\nContent-Length: 6\r\n\r\n"
        )
        # Without the bounded read, the server has closed by now, and these
        # bytes make the kernel reset the connection before the client reads.
        time.sleep(0.1)
        client.sendall(b"unused")
        time.sleep(0.1)
        received = b""
        while chunk := client.recv(65536):
            received += chunk
        return received


@pytest.mark.parametrize("parser", ["h11", "httptools"])
async def test_draining_rejection_survives_a_body_that_follows_the_headers(parser):
    server, serving, sock, requests = await _serve_draining(parser)
    try:
        async with asyncio.timeout(5):
            received = await asyncio.to_thread(_late_body_exchange, sock.getsockname())
        head, body = received.split(b"\r\n\r\n", 1)
        lines = head.decode("ascii").split("\r\n")
        headers = dict(line.lower().split(": ", 1) for line in lines[1:])
        assert lines[0].split()[1] == "503" and headers["retry-after"] == "1"
        assert headers["connection"] == "close"
        assert json.loads(body)["error"]["code"] == "gateway_draining"
        assert not requests
    finally:
        await _stop(server, serving, sock, None)


@pytest.mark.parametrize("parser", ["h11", "httptools"])
@pytest.mark.parametrize("length", [64 * 1024 + 1, None])
async def test_draining_rejection_does_not_wait_for_a_large_or_unframed_body(parser, length):
    server, serving, sock, requests = await _serve_draining(parser)
    writer = None
    framing = (
        f"Content-Length: {length}" if length is not None else "Transfer-Encoding: chunked"
    ).encode()
    try:
        # Far below the discard deadline: the rejection never reads the body.
        async with asyncio.timeout(0.5):
            reader, writer = await asyncio.open_connection(*sock.getsockname())
            writer.write(
                b"POST /v1/chat/completions HTTP/1.1\r\nHost: fixture\r\n" + framing + b"\r\n\r\n"
            )
            await writer.drain()
            status, headers, body = await read_response(reader)
            assert status == 503 and headers["connection"] == "close"
            assert json.loads(body)["error"]["code"] == "gateway_draining"
            assert await reader.read() == b""
            assert not requests
    finally:
        await _stop(server, serving, sock, writer)


async def test_draining_rejection_stops_waiting_for_a_slow_small_body(monkeypatch):
    monkeypatch.setattr("src.middleware.ingress.DRAINING_DISCARD_SECONDS", 0.05)
    server, serving, sock, requests = await _serve_draining("httptools")
    writer = None
    try:
        async with asyncio.timeout(2):
            reader, writer = await asyncio.open_connection(*sock.getsockname())
            writer.write(
                b"POST /v1/chat/completions HTTP/1.1\r\nHost: fixture\r\nContent-Length: 6\r\n\r\n"
            )
            await writer.drain()
            status, headers, body = await read_response(reader)
            assert status == 503 and headers["connection"] == "close"
            assert json.loads(body)["error"]["code"] == "gateway_draining"
            assert await reader.read() == b""
    finally:
        await _stop(server, serving, sock, writer)


async def _draining_status(sock, head):
    reader, writer = await asyncio.open_connection(*sock.getsockname())
    writer.write(head)
    await writer.drain()
    status, headers, body = await read_response(reader)
    writer.close()
    await asyncio.gather(writer.wait_closed(), return_exceptions=True)
    return status, headers, json.loads(body)["error"]["code"]


async def test_draining_rejection_does_not_wait_for_mixed_framing_with_h11():
    # h11 keeps both headers and reads the body as chunked.
    server, serving, sock, requests = await _serve_draining("h11")
    try:
        async with asyncio.timeout(0.5):
            status, headers, code = await _draining_status(
                sock,
                b"POST /v1/chat/completions HTTP/1.1\r\nHost: fixture\r\n"
                b"Transfer-Encoding: chunked\r\nContent-Length: 1\r\n\r\n",
            )
        assert (status, headers["connection"], code) == (503, "close", "gateway_draining")
        assert not requests
    finally:
        await _stop(server, serving, sock, None)


async def test_draining_rejection_skips_the_discard_when_every_slot_is_in_use(monkeypatch):
    monkeypatch.setattr("src.middleware.ingress.DRAINING_DISCARD_SECONDS", 3.0)
    server, serving, sock, requests = await _serve_draining("httptools", slots=1)
    head = b"POST /v1/chat/completions HTTP/1.1\r\nHost: fixture\r\nContent-Length: 6\r\n\r\n"
    holder = None
    try:
        async with asyncio.timeout(2):
            # The first partial upload holds the only slot while it waits for its body.
            holder_reader, holder = await asyncio.open_connection(*sock.getsockname())
            holder.write(head + b"unu")
            await holder.drain()
            while not server.server_state.tasks:
                await asyncio.sleep(0)
            await asyncio.sleep(0.05)
            # A second partial upload gets its response without a discard.
            async with asyncio.timeout(0.5):
                status, headers, code = await _draining_status(sock, head + b"unu")
            assert (status, headers["connection"], code) == (503, "close", "gateway_draining")
            holder.write(b"sed")
            await holder.drain()
            status, headers, body = await read_response(holder_reader)
            assert status == 503 and json.loads(body)["error"]["code"] == "gateway_draining"
            assert not requests
    finally:
        await _stop(server, serving, sock, holder)
