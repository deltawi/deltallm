"""Resolve correlation IDs once without changing HTTP bodies or other headers."""

from uuid import UUID

import pytest

from src.middleware.request_identity import RequestIdentityMiddleware
from src.request_identity import resolve_request_id, valid_request_id


@pytest.mark.parametrize("value", [None, "", " ", "a" * 257, "a\n", "é", 1, []])
def test_invalid_correlation_ids_are_not_accepted(value):
    assert not valid_request_id(value)


@pytest.mark.parametrize("value", ["trace-1", "a" * 256, "caller:request/123"])
def test_valid_correlation_ids_are_preserved(value):
    assert resolve_request_id(value) == value


async def test_generated_ids_are_distinct():
    first, second = resolve_request_id(None), resolve_request_id(None)
    assert first != second
    assert UUID(first).version == UUID(second).version == 4


@pytest.mark.parametrize(
    "supplied",
    [
        [],
        [(b"x-request-id", b"")],
        [(b"x-request-id", b"a" * 257)],
        [(b"x-request-id", b"first"), (b"X-Request-ID", b"second")],
        [(b"x-request-id", b"\xff")],
        [(b"x-request-id", b"bad id")],
    ],
)
async def test_http_input_and_stream_response_share_one_generated_id(supplied):
    sent, observed = [], []
    request_body = {"type": "http.request", "body": b"unchanged", "more_body": False}

    async def receive():
        return request_body

    async def send(message):
        sent.append(message)

    async def application(scope, receive, send):
        observed.extend(scope["headers"])
        assert await receive() is request_body
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"x-request-id", b"upstream"),
                    (b"content-type", b"text/event-stream"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"data: first\n\n", "more_body": True})
        await send({"type": "http.response.body", "body": b"data: [DONE]\n\n", "more_body": False})

    await RequestIdentityMiddleware(application)(
        {"type": "http", "headers": supplied + [(b"authorization", b"test-only")]}, receive, send
    )
    ids = [value for key, value in observed if key == b"x-request-id"]
    assert len(ids) == 1 and UUID(ids[0].decode()).version == 4
    assert (b"authorization", b"test-only") in observed
    assert dict(sent[0]["headers"])[b"x-request-id"] == ids[0]
    assert sent[1]["body"] == b"data: first\n\n"
    assert sent[2]["body"] == b"data: [DONE]\n\n"


async def test_non_http_scopes_are_unchanged():
    called = []

    async def application(scope, receive, send):
        called.append(scope)

    scope = {"type": "lifespan"}
    await RequestIdentityMiddleware(application)(scope, None, None)
    assert called == [scope] and "headers" not in scope
