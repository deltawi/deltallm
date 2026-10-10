import asyncio
import json
from unittest.mock import Mock, call

import httpx
import pytest

from src.models.errors import ProxyError
from src.providers.anthropic import AnthropicAdapter


def final_events(count):
    return [
        {"type": "message_start", "message": {"id": "c", "model": "claude"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": count},
        },
    ]


async def event_lines(events):
    for event in events:
        yield "data: " + json.dumps(event)


@pytest.mark.parametrize("count", [0, 7])
@pytest.mark.parametrize("termination", ["read-error", "eof", "disconnect"])
async def test_terminal_anthropic_usage_survives_missing_message_stop(count, termination):
    waiting = asyncio.Event()
    observer = Mock()

    async def broken_stream():
        async for line in event_lines(final_events(count)):
            yield line
        waiting.set()
        if termination == "read-error":
            raise httpx.ReadError("Connection failed after terminal usage")
        if termination == "disconnect":
            await asyncio.Event().wait()

    iterator = AnthropicAdapter(None).translate_stream(broken_stream(), output_observer=observer)
    try:
        await anext(iterator)  # Role.
        await anext(iterator)  # Content.
        if termination == "disconnect":
            pending = asyncio.create_task(anext(iterator))
            try:
                await asyncio.wait_for(waiting.wait(), 1)
                observer.assert_called_once_with(count)
            finally:
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
        else:
            with pytest.raises(httpx.ReadError if termination == "read-error" else ProxyError):
                await anext(iterator)
    finally:
        await iterator.aclose()
    observer.assert_called_once_with(count)


@pytest.mark.parametrize(
    "later,aborts",
    [
        ("data: {", True),
        ("data: []", True),
        ("data: [DONE]", True),
        ('data: {"type":"error","error":{"type":"api_error","message":"failed"}}', True),
        ('data: {"type":"message_start","message":{}}', True),
        ('data: {"type":"content_block_delta","index":0,"delta":{"text":"more"}}', False),
        ('data: {"type":"message_delta","delta":{}}', False),
    ],
    ids=["json", "payload", "done", "error", "duplicate-start", "more-output", "partial-delta"],
)
@pytest.mark.parametrize("count", [0, 7])
async def test_later_anthropic_data_invalidates_terminal_output(later, aborts, count):
    async def invalid_stream():
        async for line in event_lines(final_events(count)):
            yield line
        yield later
        yield 'data: {"type":"message_stop"}'

    observer = Mock()

    async def consume():
        return [
            line
            async for line in AnthropicAdapter(None).translate_stream(
                invalid_stream(), output_observer=observer
            )
        ]

    if aborts:
        with pytest.raises(ProxyError):
            await consume()
    else:
        assert (await consume())[-1] == "data: [DONE]"
    assert observer.call_args_list == [call(count), call(None)]


async def test_anthropic_keepalive_and_message_stop_do_not_report_usage_twice():
    events = [*final_events(7), {"type": "ping"}, {"type": "message_stop"}]
    observer = Mock()
    out = [
        line
        async for line in AnthropicAdapter(None).translate_stream(
            event_lines(events), output_observer=observer
        )
    ]
    assert out[-1] == "data: [DONE]"
    observer.assert_called_once_with(7)
