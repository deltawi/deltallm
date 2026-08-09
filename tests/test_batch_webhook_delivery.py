from __future__ import annotations

import asyncio
import contextlib
import logging

import httpx
import pytest

from src.batch.webhooks.delivery import (
    BatchWebhookHTTPSender,
    BatchWebhookTransportError,
)
from src.batch.webhooks.models import parse_batch_webhook_request
from src.batch.webhooks.network_policy import (
    BatchWebhookNetworkPolicy,
    ResolvedBatchWebhookTarget,
)


def _target() -> ResolvedBatchWebhookTarget:
    return ResolvedBatchWebhookTarget(
        connection_url="https://93.184.216.34/hook",
        host_header="customer.example",
        sni_hostname="customer.example",
        address=__import__("ipaddress").ip_address("93.184.216.34"),
    )


@pytest.mark.asyncio
async def test_low_level_sender_posts_once_without_following_redirects() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            headers={"Location": "https://private.example/redirect"},
            content=b"redirect",
        )

    sender = BatchWebhookHTTPSender(
        transport=httpx.MockTransport(handler),
        timeout_seconds=7,
    )
    response = await sender.send(
        target=_target(),
        raw_body=b'{"id":"evt-1"}',
        headers={"X-DeltaLLM-Event-Id": "evt-1"},
    )

    assert response.status_code == 302
    assert len(requests) == 1
    assert requests[0].url == httpx.URL("https://93.184.216.34/hook")
    assert requests[0].headers["host"] == "customer.example"
    assert requests[0].extensions["sni_hostname"] == "customer.example"
    assert requests[0].extensions["timeout"] == {
        "connect": 7.0,
        "read": 7.0,
        "write": 7.0,
        "pool": 7.0,
    }
    assert "authorization" not in requests[0].headers
    assert "cookie" not in requests[0].headers
    assert await requests[0].aread() == b'{"id":"evt-1"}'


class _CountingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.yielded = 0
        self.closed = False
        self._chunks = iter((b"1234", b"5678", b"not-read"))

    def __aiter__(self):  # noqa: ANN204
        return self

    async def __anext__(self) -> bytes:
        try:
            chunk = next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration from None
        self.yielded += 1
        return chunk

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_low_level_sender_bounds_response_read_and_closes_stream() -> None:
    stream = _CountingStream()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    sender = BatchWebhookHTTPSender(
        transport=transport,
        timeout_seconds=1,
        max_response_bytes=5,
    )

    response = await sender.send(target=_target(), raw_body=b"{}", headers={})

    assert response.status_code == 200
    assert stream.yielded == 2
    assert stream.closed is True


@pytest.mark.asyncio
async def test_low_level_sender_bounds_stalled_response_cleanup() -> None:
    close_started = asyncio.Event()
    close_cancelled = asyncio.Event()

    class _BlockingCloseStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.yielded = False

        def __aiter__(self):  # noqa: ANN204
            return self

        async def __anext__(self) -> bytes:
            if self.yielded:
                raise StopAsyncIteration
            self.yielded = True
            return b"response"

        async def aclose(self) -> None:
            close_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                close_cancelled.set()
                raise

    sender = BatchWebhookHTTPSender(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(204, stream=_BlockingCloseStream())
        ),
        timeout_seconds=0.02,
        max_response_bytes=1,
    )

    response = await asyncio.wait_for(
        sender.send(target=_target(), raw_body=b"{}", headers={}),
        timeout=0.5,
    )

    assert response.status_code == 204
    assert close_started.is_set()
    assert close_cancelled.is_set()


@pytest.mark.asyncio
async def test_low_level_sender_keeps_received_status_when_cleanup_fails() -> None:
    class _BrokenCloseStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.yielded = False

        def __aiter__(self):  # noqa: ANN204
            return self

        async def __anext__(self) -> bytes:
            if self.yielded:
                raise StopAsyncIteration
            self.yielded = True
            return b"response"

        async def aclose(self) -> None:
            raise RuntimeError("sensitive cleanup failure")

    sender = BatchWebhookHTTPSender(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(202, stream=_BrokenCloseStream())
        ),
        timeout_seconds=1,
        max_response_bytes=1,
    )

    response = await sender.send(target=_target(), raw_body=b"{}", headers={})

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_low_level_sender_normalizes_transport_errors_without_url_text() -> None:
    sensitive = "https://secret.example/private-path"

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout(sensitive, request=request)

    sender = BatchWebhookHTTPSender(
        transport=httpx.MockTransport(handler),
        timeout_seconds=1,
    )
    with pytest.raises(BatchWebhookTransportError) as exc_info:
        await sender.send(target=_target(), raw_body=b"{}", headers={})

    assert exc_info.value.reason == "connect_timeout"
    assert sensitive not in str(exc_info.value)


@pytest.mark.asyncio
async def test_low_level_sender_suppresses_sensitive_httpcore_debug_traces(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hostname = "sensitive-customer.example"
    response_header = "private-response-header-value"

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        logging.getLogger("httpcore.connection").debug(
            "start_tls server_hostname=%s",
            hostname,
        )
        logging.getLogger("httpcore.http11").debug(
            "receive_response_headers headers=%s",
            response_header,
        )
        return httpx.Response(204)

    sender = BatchWebhookHTTPSender(
        transport=httpx.MockTransport(handler),
        timeout_seconds=1,
    )
    with (
        caplog.at_level(logging.DEBUG, logger="httpcore.connection"),
        caplog.at_level(logging.DEBUG, logger="httpcore.http11"),
    ):
        response = await sender.send(target=_target(), raw_body=b"{}", headers={})
        logging.getLogger("httpcore.http11").debug("httpcore-debug-restored")

    assert response.status_code == 204
    assert hostname not in caplog.text
    assert response_header not in caplog.text
    assert "httpcore-debug-restored" in caplog.text


@pytest.mark.asyncio
async def test_httpcore_debug_guard_remains_active_for_concurrent_attempts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        call = request.headers["X-Test-Call"]
        logging.getLogger("httpcore.connection").debug("sensitive-%s", call)
        if call == "first":
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
            await release_second.wait()
        return httpx.Response(204)

    sender = BatchWebhookHTTPSender(
        transport=httpx.MockTransport(handler),
        timeout_seconds=1,
    )
    with caplog.at_level(logging.DEBUG, logger="httpcore"):
        first = asyncio.create_task(
            sender.send(
                target=_target(),
                raw_body=b"{}",
                headers={"X-Test-Call": "first"},
            )
        )
        await asyncio.wait_for(first_started.wait(), timeout=1)
        second = asyncio.create_task(
            sender.send(
                target=_target(),
                raw_body=b"{}",
                headers={"X-Test-Call": "second"},
            )
        )
        await asyncio.wait_for(second_started.wait(), timeout=1)

        release_first.set()
        await asyncio.wait_for(first, timeout=1)
        logging.getLogger("httpcore.http11").debug("still-sensitive")
        release_second.set()
        await asyncio.wait_for(second, timeout=1)
        logging.getLogger("httpcore.http11").debug("guard-restored")

    assert "sensitive-first" not in caplog.text
    assert "sensitive-second" not in caplog.text
    assert "still-sensitive" not in caplog.text
    assert "guard-restored" in caplog.text


@pytest.mark.asyncio
async def test_httpcore_debug_guard_restores_after_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        logging.getLogger("httpcore.connection").debug("sensitive-cancelled-attempt")
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    sender = BatchWebhookHTTPSender(
        transport=httpx.MockTransport(handler),
        timeout_seconds=30,
    )
    with caplog.at_level(logging.DEBUG, logger="httpcore"):
        task = asyncio.create_task(sender.send(target=_target(), raw_body=b"{}", headers={}))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        logging.getLogger("httpcore.http11").debug("guard-restored-after-cancel")

    assert "sensitive-cancelled-attempt" not in caplog.text
    assert "guard-restored-after-cancel" in caplog.text


@pytest.mark.asyncio
async def test_low_level_sender_bounds_the_whole_request_attempt() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        await asyncio.sleep(1)
        return httpx.Response(200)

    sender = BatchWebhookHTTPSender(
        transport=httpx.MockTransport(handler),
        timeout_seconds=0.01,
    )
    with pytest.raises(BatchWebhookTransportError) as exc_info:
        await sender.send(target=_target(), raw_body=b"{}", headers={})

    assert exc_info.value.reason == "request_timeout"


@pytest.mark.asyncio
async def test_low_level_sender_keeps_received_status_when_body_stalls() -> None:
    class _SlowBody(httpx.AsyncByteStream):
        def __aiter__(self):  # noqa: ANN204
            return self

        async def __anext__(self) -> bytes:
            await asyncio.sleep(1)
            raise StopAsyncIteration

    sender = BatchWebhookHTTPSender(
        transport=httpx.MockTransport(lambda request: httpx.Response(202, stream=_SlowBody())),
        timeout_seconds=0.01,
    )

    response = await sender.send(target=_target(), raw_body=b"{}", headers={})

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_low_level_sender_keeps_received_status_when_body_read_fails() -> None:
    class _BrokenBody(httpx.AsyncByteStream):
        def __aiter__(self):  # noqa: ANN204
            return self

        async def __anext__(self) -> bytes:
            raise httpx.ReadError("sensitive upstream detail")

    sender = BatchWebhookHTTPSender(
        transport=httpx.MockTransport(lambda request: httpx.Response(204, stream=_BrokenBody())),
        timeout_seconds=1,
    )

    response = await sender.send(target=_target(), raw_body=b"{}", headers={})

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_low_level_sender_delivers_to_policy_pinned_address_end_to_end() -> None:
    received: list[bytes] = []

    async def receive(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        request = b""
        while b"\r\n\r\n" not in request:
            request += await reader.read(4_096)
        headers, body = request.split(b"\r\n\r\n", 1)
        content_length = 0
        for line in headers.split(b"\r\n")[1:]:
            name, _, value = line.partition(b":")
            if name.lower() == b"content-length":
                content_length = int(value.strip())
        if len(body) < content_length:
            body += await reader.readexactly(content_length - len(body))
        received.append(headers + b"\r\n\r\n" + body)
        writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

    try:
        server = await asyncio.start_server(receive, host="127.0.0.1", port=0)
    except PermissionError:
        pytest.skip("local socket binding is unavailable in this test environment")
    port = int(server.sockets[0].getsockname()[1])

    async def resolver(hostname: str, resolved_port: int) -> tuple[str, ...]:
        assert (hostname, resolved_port) == ("customer.example", port)
        return ("127.0.0.1",)

    policy = BatchWebhookNetworkPolicy(
        allow_http=True,
        allowed_ports=[port],
        allowed_private_cidrs=["127.0.0.0/8"],
        resolver=resolver,
    )
    config = parse_batch_webhook_request(
        {
            "url": f"http://customer.example:{port}/webhook?event=batch",
            "signing_secret": "s" * 32,
        },
        allow_http=True,
        allowed_ports=[port],
    )
    target = await policy.resolve(config, attempt_count=1)
    transport = httpx.AsyncHTTPTransport(
        retries=0,
        trust_env=False,
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
    )
    sender = BatchWebhookHTTPSender(transport=transport, timeout_seconds=1)
    try:
        response = await sender.send(
            target=target,
            raw_body=b'{"id":"evt-1"}',
            headers={"X-DeltaLLM-Event-Id": "evt-1"},
        )
    finally:
        await transport.aclose()
        server.close()
        await server.wait_closed()

    assert response.status_code == 204
    assert len(received) == 1
    assert received[0].startswith(b"POST /webhook?event=batch HTTP/1.1\r\n")
    assert f"host: customer.example:{port}\r\n".encode() in received[0].lower()
    assert received[0].endswith(b'{"id":"evt-1"}')
