"""Accept only valid W3C trace context and scope it to one HTTP request."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.callbacks.payload import build_standard_logging_payload
from src.middleware.request_identity import RequestIdentityMiddleware
from src.telemetry.trace_context import (
    bind_trace_context,
    current_trace_context,
    parse_trace_context,
    reset_trace_context,
    trace_context_from_headers,
)

pytestmark = pytest.mark.hermetic

VALID = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        1,
        VALID.upper(),
        "01-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        "00-00000000000000000000000000000000-b7ad6b7169203331-01",
        "00-0af7651916cd43dd8448eb211c80319c-0000000000000000-01",
        "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01-extra",
        VALID + "\n",
    ],
)
def test_invalid_traceparent_is_ignored(value):
    assert parse_trace_context(value) is None


def test_tracestate_is_kept_only_when_bounded_and_printable():
    assert parse_trace_context(VALID, "vendor=1").carrier() == {
        "traceparent": VALID,
        "tracestate": "vendor=1",
    }
    assert parse_trace_context(VALID, "a" * 513).carrier() == {"traceparent": VALID}
    assert parse_trace_context(VALID, "bad\nstate").carrier() == {"traceparent": VALID}


def test_repeated_traceparent_header_is_ambiguous():
    headers = [(b"traceparent", VALID.encode()), (b"Traceparent", VALID.encode())]
    assert trace_context_from_headers(headers) is None
    assert trace_context_from_headers([(b"TraceParent", VALID.encode())]).traceparent == VALID


async def test_middleware_binds_header_context_for_the_request_only():
    observed = []

    async def application(scope, receive, send):
        observed.append(current_trace_context())

    middleware = RequestIdentityMiddleware(application)
    scope = {"type": "http", "headers": [(b"traceparent", VALID.encode())]}
    await middleware(scope, None, None)

    assert observed[0].traceparent == VALID
    assert current_trace_context() is None
    # The header is left for the application unchanged.
    assert (b"traceparent", VALID.encode()) in scope["headers"]


def _payload(metadata: dict) -> object:
    now = datetime.now(tz=UTC)
    return build_standard_logging_payload(
        call_type="completion",
        request_id="req-1",
        model="m",
        deployment_model=None,
        request_payload={"metadata": metadata},
        response_obj=None,
        user_api_key_dict={},
        start_time=now,
        end_time=now,
        api_base=None,
    )


def test_payload_prefers_header_context_over_metadata():
    other = "00-11111111111111111111111111111111-2222222222222222-00"
    token = bind_trace_context(parse_trace_context(VALID))
    try:
        assert _payload({"traceparent": other}).trace_context == {"traceparent": VALID}
    finally:
        reset_trace_context(token)
    assert _payload({"traceparent": other}).trace_context == {"traceparent": other}
    assert _payload({"traceparent": "not-valid"}).trace_context is None
    assert _payload({}).trace_context is None
