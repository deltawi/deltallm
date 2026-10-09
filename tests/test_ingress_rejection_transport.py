"""Retryable rejection must preserve its response and the request boundary."""

import asyncio
import json
import socket
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
