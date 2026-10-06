from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
import httpx

from src.providers.openai import OpenAIAdapter
from src.providers.azure import AzureOpenAIAdapter
from src.providers.anthropic import AnthropicAdapter
from src.providers.gemini import GeminiAdapter
from src.providers.bedrock import BedrockAdapter
from src.providers.profiled_chat import ProfiledChatAdapter
from src.providers.chat_profiles import CHAT_PROVIDER_PROFILES
from src.providers.output_usage import compatible_output_count


@pytest.mark.parametrize(
    "usage,expected",
    [
        ({"completion_tokens": 0}, 0),
        ({"completion_tokens": 5, "completion_tokens_details": {"reasoning_tokens": 3}}, 5),
        ({"completion_tokens": True}, None),
        ({"completion_tokens": 2**31}, None),
        ({"prompt_tokens": 5}, None),
    ],
)
def test_compatible_output_excludes_input_and_never_adds_reasoning_twice(usage, expected):
    assert compatible_output_count({"usage": usage}) == expected


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"x_groq": {"usage": {"completion_tokens": 5}}}, 5),
        ({"usage": {"completion_tokens": 5}, "x_groq": {"usage": {"completion_tokens": 5}}}, 5),
        ({"usage": {"completion_tokens": 5}, "x_groq": {"usage": {"completion_tokens": 6}}}, None),
        ({"usage": {"completion_tokens": 5}, "x_groq": {"error": {"message": "failure"}}}, None),
        (
            {"usage": {"completion_tokens": "5"}, "x_groq": {"usage": {"completion_tokens": 5}}},
            None,
        ),
    ],
)
def test_groq_evidence_supports_both_layouts_and_rejects_conflicts(payload, expected):
    assert OpenAIAdapter(None).complete_output_count(payload) == expected


def chunk(choices=None, **fields):
    return {
        "id": "c",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "test",
        "choices": choices
        if choices is not None
        else [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
        **fields,
    }


async def lines(frames, done=True):
    for frame in frames:
        yield "data: " + json.dumps(frame)
    if done:
        yield "data: [DONE]"


def adapters():
    return [
        OpenAIAdapter(None),
        AzureOpenAIAdapter(None),
        ProfiledChatAdapter(None, CHAT_PROVIDER_PROFILES["deepseek"]),
    ]


@pytest.mark.parametrize("adapter", adapters(), ids=["openai-compatible", "azure", "profile"])
@pytest.mark.parametrize(
    "layout",
    [
        "usage-only",
        "terminal-choice",
        "groq",
        "matching",
        "conflicting",
        "zero",
        "missing",
        "continuous",
    ],
)
async def test_stream_extracts_raw_final_aggregate_once(adapter, layout):
    usage = {
        "completion_tokens": 0 if layout == "zero" else 7,
        "prompt_tokens": 2,
        "total_tokens": 2 if layout == "zero" else 9,
    }
    frames = [chunk()]
    expected = 7
    if layout in {"usage-only", "zero"}:
        frames.append(chunk([], usage=usage))
    if layout == "terminal-choice":
        frames = [chunk(usage=usage)]
    if layout in {"groq", "matching", "conflicting"}:
        fields = {"x_groq": {"usage": usage}}
        if layout in {"matching", "conflicting"}:
            fields["usage"] = {
                **usage,
                "completion_tokens": 8 if layout == "conflicting" else 7,
                "total_tokens": 10 if layout == "conflicting" else 9,
            }
        frames = [chunk(**fields)]
    if layout == "continuous":
        frames.insert(
            0,
            chunk(
                [{"index": 0, "delta": {"content": "partial"}, "finish_reason": None}],
                usage={**usage, "completion_tokens": 3, "total_tokens": 5},
            ),
        )
        frames.append(chunk([], usage=usage))
    if layout in {"missing", "conflicting"}:
        expected = None
    if layout == "zero":
        expected = 0
    observer = Mock()
    out = [
        line
        async for line in adapter.translate_stream(
            lines(frames), model_name="test", output_observer=observer
        )
    ]
    assert out[-1] == "data: [DONE]"
    observer.assert_called_once_with(expected)


@pytest.mark.parametrize("adapter", adapters(), ids=["openai-compatible", "azure", "profile"])
async def test_intermediate_stream_usage_is_not_final_evidence(adapter):
    frames = [
        chunk(
            [{"index": 0, "delta": {"content": "partial"}, "finish_reason": None}],
            usage={"completion_tokens": 3, "prompt_tokens": 2, "total_tokens": 5},
        ),
        chunk(),
    ]
    observer = Mock()
    [line async for line in adapter.translate_stream(lines(frames), output_observer=observer)]
    observer.assert_called_once_with(None)


async def test_multiple_choices_count_final_aggregate_and_new_choice_invalidates_early_total():
    adapter = OpenAIAdapter(None)
    frames = [
        chunk(usage={"completion_tokens": 3}),
        chunk([{"index": 1, "delta": {"content": "second"}, "finish_reason": "stop"}]),
        chunk([], usage={"completion_tokens": 8}),
    ]
    observer = Mock()
    [line async for line in adapter.translate_stream(lines(frames), output_observer=observer)]
    observer.assert_called_once_with(8)
    observer.reset_mock()
    [line async for line in adapter.translate_stream(lines(frames[:-1]), output_observer=observer)]
    observer.assert_called_once_with(None)


async def test_partial_stream_with_usage_never_reports_complete_output():
    from src.models.errors import ProxyError

    adapter = OpenAIAdapter(None)
    observer = Mock()
    frames = [
        chunk([{"index": 0, "delta": {"content": "partial"}, "finish_reason": None}]),
        chunk([], usage={"completion_tokens": 3}),
    ]
    with pytest.raises(ProxyError):
        [
            line
            async for line in adapter.translate_stream(
                lines(frames, done=False), output_observer=observer
            )
        ]
    observer.assert_not_called()


@pytest.mark.parametrize(
    "adapter,payload,expected",
    [
        (
            AnthropicAdapter(None),
            {"usage": {"output_tokens": 7, "input_tokens": 11, "cache_read_input_tokens": 13}},
            7,
        ),
        (
            BedrockAdapter(None),
            {"usage": {"outputTokens": 7, "inputTokens": 11, "totalTokens": 18}},
            7,
        ),
        (
            GeminiAdapter(None),
            {
                "usageMetadata": {
                    "candidatesTokenCount": 4,
                    "thoughtsTokenCount": 3,
                    "promptTokenCount": 11,
                    "totalTokenCount": 18,
                }
            },
            7,
        ),
        (
            GeminiAdapter(None),
            {
                "usageMetadata": {
                    "candidatesTokenCount": 4,
                    "promptTokenCount": 11,
                    "totalTokenCount": 15,
                }
            },
            4,
        ),
        (
            GeminiAdapter(None),
            {
                "usageMetadata": {
                    "candidatesTokenCount": 4,
                    "promptTokenCount": 11,
                    "totalTokenCount": 18,
                }
            },
            None,
        ),
        (GeminiAdapter(None), {"usageMetadata": {"candidatesTokenCount": 4}}, None),
        (
            GeminiAdapter(None),
            {"usageMetadata": {"candidatesTokenCount": 4, "thoughtsTokenCount": False}},
            None,
        ),
        (BedrockAdapter(None), {"usage": {"inputTokens": 11}}, None),
    ],
)
def test_native_usage_is_provider_owned_raw_output(adapter, payload, expected):
    assert adapter.complete_output_count(payload) == expected


@pytest.mark.parametrize("adapter", [OpenAIAdapter(None), AzureOpenAIAdapter(None)])
async def test_json_all_choices_share_one_complete_provider_output_count(adapter):
    payload = {
        "id": "c",
        "object": "chat.completion",
        "created": 1,
        "model": "test",
        "choices": [
            {"index": i, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
            for i in range(2)
        ],
        "usage": {"prompt_tokens": 2, "completion_tokens": 7, "total_tokens": 9},
    }
    observer = Mock()
    await adapter.translate_success_response(
        httpx.Response(200, json=payload), "test", output_observer=observer
    )
    observer.assert_called_once_with(7)


@pytest.mark.parametrize("output_count", [0, 7, None])
async def test_bedrock_stream_reads_native_final_metadata_once(output_count):
    from tests.test_provider_compat import _encode_eventstream_message, _byte_stream

    events = [
        ("messageStart", {"role": "assistant"}),
        ("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "ok"}}),
        ("messageStop", {"stopReason": "end_turn"}),
    ]
    if output_count is not None:
        events.append(
            (
                "metadata",
                {
                    "usage": {
                        "inputTokens": 2,
                        "outputTokens": output_count,
                        "totalTokens": 2 + output_count,
                    }
                },
            )
        )
    frames = b"".join(
        _encode_eventstream_message({":message-type": "event", ":event-type": kind}, payload)
        for kind, payload in events
    )
    observer = Mock()
    out = [
        line
        async for line in BedrockAdapter(None).translate_stream(
            _byte_stream([frames[:17], frames[17:]]), output_observer=observer
        )
    ]
    assert out[-1] == "data: [DONE]"
    observer.assert_called_once_with(output_count)


def test_error_usage_is_not_complete_output_evidence():
    assert (
        compatible_output_count({"error": {"message": "failed"}, "usage": {"completion_tokens": 0}})
        is None
    )


@pytest.mark.parametrize("field", ["inputTokens", "totalTokens"])
@pytest.mark.parametrize("bad_value", [None, True, -1, "2"])
@pytest.mark.parametrize("output_count", [0, 7])
async def test_bedrock_invalid_other_usage_preserves_raw_output(field, bad_value, output_count):
    from src.models.errors import ProxyError
    from tests.test_provider_compat import _encode_eventstream_message, _byte_stream

    usage = {"inputTokens": 2, "outputTokens": output_count, "totalTokens": 2 + output_count}
    if bad_value is None:
        del usage[field]
    else:
        usage[field] = bad_value
    events = [
        ("messageStart", {"role": "assistant"}),
        ("messageStop", {"stopReason": "end_turn"}),
        ("metadata", {"usage": usage}),
    ]
    frames = b"".join(
        _encode_eventstream_message({":message-type": "event", ":event-type": kind}, payload)
        for kind, payload in events
    )
    observer = Mock()
    with pytest.raises(ProxyError, match="Provider returned an invalid response") as error:
        [
            line
            async for line in BedrockAdapter(None).translate_stream(
                _byte_stream([frames]), output_observer=observer
            )
        ]
    assert error.value.status_code == 503
    observer.assert_called_once_with(output_count)


@pytest.mark.parametrize("output_count", [None, True, -1, 1.5, "7", 2**31])
async def test_bedrock_invalid_output_stays_unknown(output_count):
    from src.models.errors import ProxyError
    from tests.test_provider_compat import _encode_eventstream_message, _byte_stream

    usage = {"inputTokens": 2}
    if output_count is not None:
        usage["outputTokens"] = output_count
    events = [
        ("messageStart", {"role": "assistant"}),
        ("messageStop", {"stopReason": "end_turn"}),
        ("metadata", {"usage": usage}),
    ]
    frames = b"".join(
        _encode_eventstream_message({":message-type": "event", ":event-type": kind}, payload)
        for kind, payload in events
    )
    observer = Mock()
    with pytest.raises(ProxyError, match="Provider returned an invalid response") as error:
        [
            line
            async for line in BedrockAdapter(None).translate_stream(
                _byte_stream([frames]), output_observer=observer
            )
        ]
    assert error.value.status_code == 503
    observer.assert_called_once_with(None)


@pytest.mark.parametrize("has_content", [False, True], ids=["empty", "text"])
@pytest.mark.parametrize("output_count", [0, 7, None])
async def test_bedrock_retains_final_usage_before_downstream_delivery(has_content, output_count):
    from tests.test_provider_compat import _encode_eventstream_message, _byte_stream

    events = [("messageStart", {"role": "assistant"})]
    if has_content:
        events.append(("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "ok"}}))
    events.append(("messageStop", {"stopReason": "end_turn"}))
    if output_count is not None:
        events.append(
            (
                "metadata",
                {
                    "usage": {
                        "inputTokens": 2,
                        "outputTokens": output_count,
                        "totalTokens": 2 + output_count,
                    }
                },
            )
        )
    frames = b"".join(
        _encode_eventstream_message({":message-type": "event", ":event-type": kind}, payload)
        for kind, payload in events
    )
    observer = Mock()
    iterator = BedrockAdapter(None).translate_stream(
        _byte_stream([frames]), output_observer=observer
    )
    try:
        if has_content:
            await anext(iterator)  # Role and content precede provider completion.
            await anext(iterator)
            observer.assert_not_called()
        final = await anext(iterator)
        assert final.startswith("data: ") and final != "data: [DONE]"
        observer.assert_called_once_with(output_count)
    finally:
        # Close at the first frame sent after complete upstream EOF.
        await iterator.aclose()
    observer.assert_called_once_with(output_count)


async def test_more_output_after_a_terminal_usage_invalidates_that_evidence():
    observer = Mock()
    frames = [
        chunk(usage={"completion_tokens": 3}),
        chunk([{"index": 0, "delta": {"content": "later"}, "finish_reason": None}]),
    ]
    [
        line
        async for line in OpenAIAdapter(None).translate_stream(
            lines(frames), output_observer=observer
        )
    ]
    observer.assert_called_once_with(None)
