import asyncio
import json

import httpx
import pytest

from tests.router.selection.test_realtime import configure

pytestmark = pytest.mark.app


async def test_http_disconnect_during_selector_cancels_provider_before_response_headers(test_app):
    started, closed = asyncio.Event(), asyncio.Event()
    calls = []

    async def provider(request):
        calls.append(json.loads(request.content)["model"])
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    receive_queue = asyncio.Queue()
    body = json.dumps(
        {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": True}
    ).encode()
    receive_queue.put_nowait({"type": "http.request", "body": body, "more_body": False})
    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "root_path": "",
        "server": ("test", 80),
        "client": ("127.0.0.1", 1234),
        "headers": [
            (b"authorization", b"Bearer sk-test"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        billing, _ = configure(test_app, upstream)
        task = asyncio.create_task(test_app(scope, receive_queue.get, send))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            receive_queue.put_nowait({"type": "http.disconnect"})
            await asyncio.wait_for(task, timeout=2)
            assert closed.is_set() and calls == ["classifier"]
            starts = [message for message in sent if message["type"] == "http.response.start"]
            assert len(starts) == 1 and starts[0]["status"] == 499
            assert not any(b"text/event-stream" in value for _, value in starts[0]["headers"])
            billing.dispatch.assert_awaited_once()
            billing.accept_selector.assert_not_awaited()
            billing.unattempted.assert_not_awaited()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
