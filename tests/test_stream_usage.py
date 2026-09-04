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
    assert resolved.estimate_incomplete is False


def test_stream_usage_tracker_counts_reasoning_only_output() -> None:
    tracker = StreamUsageTracker()

    tracker.add_line(
        'data: {"choices":[{"index":0,"delta":{"reasoning_content":"thinking"},'
        '"finish_reason":null}]}'
    )
    resolved = tracker.resolve(_payload())

    assert resolved.usage == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    assert resolved.estimate_incomplete is False


def test_stream_usage_tracker_does_not_double_count_reasoning_aliases() -> None:
    tracker = StreamUsageTracker()

    tracker.add_line(
        'data: {"choices":[{"index":0,"delta":{"reasoning":"abcdefgh",'
        '"reasoning_content":"abcdefgh","reasoning_details":'
        '[{"type":"reasoning.text","text":"abcdefgh"}]},"finish_reason":null}]}'
    )
    resolved = tracker.resolve(_payload())

    assert resolved.usage == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}


def test_stream_usage_tracker_sums_reasoning_when_representation_changes_between_deltas() -> None:
    tracker = StreamUsageTracker()

    tracker.add_line(
        'data: {"choices":[{"index":0,"delta":{"reasoning":"abcdefgh"},"finish_reason":null}]}'
    )
    tracker.add_line(
        'data: {"choices":[{"index":0,"delta":{"reasoning_content":"ijklmnop"},'
        '"finish_reason":null}]}'
    )
    tracker.add_line(
        'data: {"choices":[{"index":0,"delta":{"reasoning_details":'
        '[{"type":"reasoning.text","text":"qrstuvwx"}]},"finish_reason":null}]}'
    )
    resolved = tracker.resolve(_payload())

    assert resolved.usage == {"prompt_tokens": 3, "completion_tokens": 6, "total_tokens": 9}


def test_stream_usage_tracker_counts_reasoning_detail_text_and_summary() -> None:
    tracker = StreamUsageTracker()

    tracker.add_line(
        'data: {"choices":[{"index":0,"delta":{"reasoning_details":'
        '[{"type":"reasoning.text","text":"step","summary":"plan"}]},'
        '"finish_reason":null}]}'
    )
    resolved = tracker.resolve(_payload())

    assert resolved.usage == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    assert resolved.estimate_incomplete is False


def test_stream_usage_tracker_conservatively_counts_encrypted_reasoning() -> None:
    tracker = StreamUsageTracker()

    tracker.add_line(
        'data: {"choices":[{"index":0,"delta":{"reasoning_details":'
        '[{"type":"reasoning.encrypted","data":"opaque"}]},"finish_reason":null}]}'
    )
    resolved = tracker.resolve(_payload())

    assert resolved.usage == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    assert resolved.estimate_incomplete is True
    assert resolved.metadata()["usage_estimate_incomplete"] is True


def test_stream_usage_tracker_conservatively_counts_structured_reasoning() -> None:
    tracker = StreamUsageTracker()

    tracker.add_line(
        'data: {"choices":[{"index":0,"delta":{"reasoning":{"data":"opaque"}},'
        '"finish_reason":null}]}'
    )
    resolved = tracker.resolve(_payload())

    assert resolved.usage == {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}
    assert resolved.estimate_incomplete is True


def test_stream_usage_tracker_provider_usage_overrides_incomplete_estimate() -> None:
    tracker = StreamUsageTracker()

    tracker.add_line(
        'data: {"choices":[{"index":0,"delta":{"reasoning_details":'
        '[{"type":"reasoning.encrypted","data":"opaque"}]},"finish_reason":null}]}'
    )
    tracker.add_line(
        'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":7,"total_tokens":10}}'
    )
    resolved = tracker.resolve(_payload())

    assert resolved.source == "provider"
    assert resolved.usage == {"prompt_tokens": 3, "completion_tokens": 7, "total_tokens": 10}
    assert resolved.estimate_incomplete is False


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


def test_stream_usage_tracker_handles_contentless_assistant_tool_call_history() -> None:
    payload = ChatCompletionRequest.model_validate(
        {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "search"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "search", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "result"},
            ],
            "stream": True,
        }
    )
    tracker = StreamUsageTracker()

    resolved = tracker.resolve(payload)

    assert resolved.source == "estimated"
    assert resolved.usage == {"prompt_tokens": 8, "completion_tokens": 0, "total_tokens": 8}
