from unittest.mock import Mock, call

import httpx
import pytest

from src.models.errors import ProxyError
from src.providers.openai_compatible import _MAX_STREAM_FRAME_CHARS
from tests.providers.test_output_usage import adapters, chunk, lines


@pytest.mark.parametrize("adapter", adapters(), ids=["compatible", "azure", "profile"])
@pytest.mark.parametrize("count", [0, 7])
@pytest.mark.parametrize("layout", ["terminal-choice", "usage-only", "groq"])
@pytest.mark.parametrize("termination", ["read-error", "disconnect"])
async def test_complete_stream_output_survives_transport_error_or_disconnect(
    adapter, count, layout, termination
):
    usage = {"completion_tokens": count, "prompt_tokens": 2, "total_tokens": 2 + count}
    frames = (
        [chunk(), chunk([], usage=usage)]
        if layout == "usage-only"
        else [chunk(**({"x_groq": {"usage": usage}} if layout == "groq" else {"usage": usage}))]
    )

    async def broken_stream():
        async for line in lines(frames, done=False):
            yield line
        raise httpx.ReadError("Connection failed after complete usage")

    observer = Mock()
    iterator = adapter.translate_stream(broken_stream(), output_observer=observer)
    try:
        for _ in frames:
            await anext(iterator)
        observer.assert_called_once_with(count)
        if termination == "read-error":
            with pytest.raises(httpx.ReadError):
                await anext(iterator)
    finally:
        await iterator.aclose()
    observer.assert_called_once_with(count)


@pytest.mark.parametrize(
    "malformed",
    [
        "data: {",
        "invalid SSE",
        "data: " + ("x" * _MAX_STREAM_FRAME_CHARS),
        'data: {"error":{"message":"Provider failed"}}',
        'data: {"choices":"invalid"}',
        'data: {"choices":[{"index":true}]}',
    ],
    ids=["json", "sse", "oversized", "provider-error", "choices", "choice-index"],
)
async def test_invalid_later_frame_clears_complete_output(malformed):
    async def invalid_stream():
        async for line in lines([chunk(usage={"completion_tokens": 7})], done=False):
            yield line
        yield malformed

    observer = Mock()
    with pytest.raises(ProxyError):
        [
            line
            async for line in adapters()[0].translate_stream(
                invalid_stream(), output_observer=observer
            )
        ]
    assert observer.call_args_list == [call(7), call(None)]


async def test_keepalive_after_complete_usage_preserves_evidence():
    async def kept_alive_stream():
        async for line in lines([chunk(usage={"completion_tokens": 7})], done=False):
            yield line
        yield ": keepalive"
        yield "event: keepalive"
        yield ""
        yield "data: "
        raise httpx.ReadError("Connection failed during keepalive")

    observer = Mock()
    with pytest.raises(httpx.ReadError):
        [
            line
            async for line in adapters()[0].translate_stream(
                kept_alive_stream(), output_observer=observer
            )
        ]
    observer.assert_called_once_with(7)
