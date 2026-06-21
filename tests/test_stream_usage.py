from __future__ import annotations

from src.chat.stream_usage import StreamUsageTracker
from src.models.requests import ChatCompletionRequest


def _payload() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hello"}],
        stream=True,
    )


def test_stream_usage_tracker_accepts_provider_zero_usage() -> None:
    tracker = StreamUsageTracker()

    tracker.add_line(
        'data: {"choices":[],"usage":{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}}'
    )
    resolved = tracker.resolve(_payload())

    assert resolved.source == "provider"
    assert resolved.usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_stream_usage_tracker_estimates_when_provider_usage_missing() -> None:
    tracker = StreamUsageTracker()

    tracker.add_line(
        'data: {"choices":[{"index":0,"delta":{"content":"done"},"finish_reason":null}]}'
    )
    resolved = tracker.resolve(_payload())

    assert resolved.source == "estimated"
    assert resolved.usage == {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}


def test_stream_usage_tracker_estimates_when_provider_usage_is_malformed() -> None:
    tracker = StreamUsageTracker()

    tracker.add_line(
        'data: {"choices":[{"index":0,"delta":{"content":"done"},"finish_reason":null}]}'
    )
    tracker.add_line(
        'data: {"choices":[],"usage":{"prompt_tokens":"bad","completion_tokens":0,"total_tokens":0}}'
    )
    resolved = tracker.resolve(_payload())

    assert resolved.source == "estimated"
    assert resolved.usage == {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}
