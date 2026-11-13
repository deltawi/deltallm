"""The stream reply accumulator rebuilds one assistant message from deltas."""

import asyncio
import gc
import json
import sys
from types import SimpleNamespace

import pytest

from src.chat.audit import stream_reply_accumulator
from src.chat.stream_reply import (
    _ENTRY_CHARS,
    _META_BYTES,
    _STR_BYTES,
    ReplyMemory,
    StreamReplyAccumulator,
    _json_size,
)
from src.chat.stream_response import ManagedStreamLifecycle
from src.db.audit.repository import AuditRepository
from src.services.audit.audit_service import AuditIngestionConfig, AuditService
from src.services.audit.payload_compaction import compact_audit_content

pytestmark = pytest.mark.hermetic


def _chunk(delta, *, finish=None, index=0):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 7,
        "model": "m",
        "choices": [{"index": index, "delta": delta, "finish_reason": finish}],
    }


def test_completion_keeps_text_reasoning_and_tool_calls() -> None:
    reply = StreamReplyAccumulator(max_chars=1000)
    for chunk in [
        _chunk({"role": "assistant"}),
        _chunk({"reasoning_content": "think "}),
        _chunk({"reasoning_content": "hard"}),
        _chunk({"content": "Hel"}),
        _chunk({"content": "lo"}),
        _chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "t1",
                        "type": "function",
                        "function": {"name": "read", "arguments": ""},
                    }
                ]
            }
        ),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"p":'}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"a"}'}}]}),
        _chunk({}, finish="tool_calls"),
        {"id": "chatcmpl-1", "choices": [], "usage": {"prompt_tokens": 3}},
    ]:
        reply.add_chunk(chunk)

    body = reply.completion(fallback_model="x", usage={"prompt_tokens": 3, "completion_tokens": 4})

    assert body == {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 7,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello",
                    "reasoning_content": "think hard",
                    "tool_calls": [
                        {
                            "id": "t1",
                            "type": "function",
                            "function": {"name": "read", "arguments": '{"p":"a"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "stream": True,
        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
    }


def test_completion_omits_empty_tool_calls_and_marks_other_choices() -> None:
    reply = StreamReplyAccumulator(max_chars=1000)
    reply.add_chunk(_chunk({"content": "kept"}, finish="stop"))
    reply.add_chunk(_chunk({"content": "dropped"}, index=1))
    reply.add_chunk(_chunk({"content": " too"}))

    body = reply.completion(fallback_model="x", usage=None)

    # Only the first choice is kept, and the marker tells that n > 1 had more.
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "kept too"}
    assert body["truncated"] is True
    assert "usage" not in body


def test_limit_drops_later_text_and_marks_truncation() -> None:
    reply = StreamReplyAccumulator(max_chars=5)
    reply.add_chunk(_chunk({"content": "abc"}))
    reply.add_chunk(_chunk({"content": "defgh"}))
    reply.add_chunk(_chunk({"content": "i"}))
    reply.add_chunk(_chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}))

    body = reply.completion(fallback_model="x", usage=None)

    assert body["choices"][0]["message"]["content"] == "abc"
    # A stopped reply does no more work, so the later tool call is not kept.
    assert "tool_calls" not in body["choices"][0]["message"]
    assert body["truncated"] is True


def test_limit_bounds_the_number_of_tool_calls() -> None:
    reply = StreamReplyAccumulator(max_chars=100_000)
    # An adapter can send many tool calls in one delta.
    reply.add_chunk(
        _chunk({"tool_calls": [{"index": i, "function": {"name": "f"}} for i in range(100)]})
    )
    for i in range(100, 200):
        reply.add_chunk(_chunk({"tool_calls": [{"index": i, "function": {"name": "f"}}]}))

    body = reply.completion(fallback_model="x", usage=None)

    assert len(body["choices"][0]["message"]["tool_calls"]) == 128
    assert body["truncated"] is True


def test_limit_bounds_the_number_of_reasoning_details() -> None:
    reply = StreamReplyAccumulator(max_chars=100_000)
    for i in range(130):
        reply.add_chunk(_chunk({"reasoning_details": [{"index": i, "text": "t"}]}))

    body = reply.completion(fallback_model="x", usage=None)

    assert len(body["choices"][0]["message"]["reasoning_details"]) == 128
    assert body["truncated"] is True


def test_long_lists_in_one_delta_are_not_read() -> None:
    reply = StreamReplyAccumulator(max_chars=100_000)
    reply.add_delta({"tool_calls": _Unreadable([{}] * 129)})
    reply.add_delta({"reasoning_details": _Unreadable([{}] * 17)})
    reply.add_delta({"content": "still kept"})

    assert reply.truncated is True
    assert reply.message()["content"] == "still kept"


def test_without_limit_every_tool_call_is_kept() -> None:
    reply = StreamReplyAccumulator()
    reply.add_delta({"tool_calls": [{"index": i, "function": {"name": "f"}} for i in range(200)]})

    assert len(reply.tool_calls()) == 200
    assert reply.truncated is False


def test_completion_is_none_without_chunks() -> None:
    assert StreamReplyAccumulator(max_chars=10).completion(fallback_model="x", usage=None) is None


def test_fallback_model_is_used_when_chunks_have_no_model() -> None:
    reply = StreamReplyAccumulator(max_chars=10)
    reply.add_chunk({"choices": [{"index": 0, "delta": {"content": "hi"}}]})

    body = reply.completion(fallback_model="group", usage=None)

    assert body["model"] == "group"
    assert body["id"] is None


def test_limit_counts_tool_call_ids_types_and_names() -> None:
    # Room for the tool call entry and five characters.
    reply = StreamReplyAccumulator(max_chars=_ENTRY_CHARS + 5)
    reply.add_delta(
        {"tool_calls": [{"index": 0, "id": "i" * 100, "type": "t" * 100, "function": {}}]}
    )
    for position in range(100):
        name = "x" if position % 2 else "y"
        reply.add_delta({"tool_calls": [{"index": 0, "function": {"name": name}}]})

    call = reply.tool_calls()[0]

    assert call["id"] == "call_0"
    assert call["type"] == "function"
    assert len(call["function"]["name"]) <= 5
    assert reply.truncated is True


def test_limit_counts_replaced_and_nested_reasoning_details() -> None:
    # The first detail takes 110 characters: the entry, its keys and values.
    reply = StreamReplyAccumulator(max_chars=120)
    reply.add_delta({"reasoning_details": [{"index": 0, "type": "reasoning.text", "text": "a"}]})
    # A repeated value is not counted again.
    reply.add_delta({"reasoning_details": [{"index": 0, "type": "reasoning.text"}]})
    assert reply._chars == 110
    reply.add_delta({"reasoning_details": [{"index": 0, "extra": {"data": "x" * 100}}]})
    reply.add_delta({"reasoning_details": [{"index": 1, "nested": ["y" * 100]}]})

    details = reply.message()["reasoning_details"]

    assert details == [{"index": 0, "type": "reasoning.text", "text": "a"}]
    assert reply.truncated is True


def test_unhashable_reasoning_index_does_not_raise() -> None:
    reply = StreamReplyAccumulator(max_chars=1000)
    reply.add_delta({"reasoning_details": [{"index": [], "text": "a"}]})
    reply.add_delta({"reasoning_details": [{"index": {}, "text": "b"}]})

    assert [item["text"] for item in reply.message()["reasoning_details"]] == ["a", "b"]


def test_completion_keeps_refusal() -> None:
    reply = StreamReplyAccumulator(max_chars=100)
    reply.add_chunk(_chunk({"refusal": "I cannot "}))
    reply.add_chunk(_chunk({"refusal": "help."}, finish="stop"))

    body = reply.completion(fallback_model="x", usage=None)

    assert body["choices"][0]["message"] == {
        "role": "assistant",
        "content": None,
        "refusal": "I cannot help.",
    }


def test_shared_memory_caps_all_replies_and_comes_back_on_release_or_free() -> None:
    # Each reply pays a fixed share for its id, model and finish reason. A string
    # costs its characters and the bytes of the string object.
    memory = ReplyMemory(2 * _META_BYTES + _STR_BYTES + 10)
    first = StreamReplyAccumulator(max_chars=100, memory=memory)
    second = StreamReplyAccumulator(max_chars=100, memory=memory)

    first.add_chunk(_chunk({"content": "abcdefgh"}))
    second.add_chunk(_chunk({"content": "abcdefgh"}))

    assert memory.used == 2 * _META_BYTES + _STR_BYTES + 8
    assert not first.truncated
    assert second.truncated
    first.release()
    assert memory.used == _META_BYTES
    # A released reply takes no more data.
    first.add_chunk(_chunk({"content": "x"}))
    assert memory.used == _META_BYTES

    third = StreamReplyAccumulator(max_chars=100, memory=memory)
    third.add_chunk(_chunk({"content": "abc"}))
    assert memory.used == 2 * _META_BYTES + _STR_BYTES + 3
    del third  # a failed or cancelled stream drops its accumulator
    gc.collect()
    assert memory.used == _META_BYTES
    second.release()
    assert memory.used == 0
    # Without room for its fixed share, a reply keeps nothing.
    full = StreamReplyAccumulator(max_chars=100, memory=ReplyMemory(_META_BYTES - 1))
    full.add_chunk(_chunk({"content": "abc"}))
    assert full.completion(fallback_model="x", usage=None)["truncated"] is True


def test_audit_reply_at_the_limit_is_cut_not_replaced_by_compaction() -> None:
    max_bytes = 64 * 1024
    service = AuditService(
        AuditRepository(None),
        ingestion_config=AuditIngestionConfig(enabled=True, payload_max_bytes=max_bytes),
    )
    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(audit_service=service)),
    )
    auth = SimpleNamespace(organization_id="org-1")

    async def build():
        reply = stream_reply_accumulator(request, auth)
        assert reply is not None
        for _ in range(200):
            reply.add_chunk(_chunk({"content": "hello world " * 50}))
        usage = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
        return reply.completion(fallback_model="m", usage=usage)

    body = asyncio.run(build())
    stored, changed = compact_audit_content(body, max_bytes=max_bytes)

    assert body["truncated"] is True
    assert changed is False
    assert stored["choices"][0]["message"]["content"].startswith("hello world")


def test_no_accumulator_when_the_org_has_content_storage_off() -> None:
    service = AuditService(
        AuditRepository(None), ingestion_config=AuditIngestionConfig(enabled=True)
    )
    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(audit_service=service)),
    )

    async def check():
        service._remember_content_policy("org-off", False, now=asyncio.get_running_loop().time())
        assert stream_reply_accumulator(request, SimpleNamespace(organization_id="org-off")) is None
        # Without an organization nothing can be stored either.
        assert stream_reply_accumulator(request, SimpleNamespace(organization_id=None)) is None
        assert (
            stream_reply_accumulator(request, SimpleNamespace(organization_id="org-new"))
            is not None
        )

    asyncio.run(check())


def test_a_non_ascii_reply_at_the_limit_is_cut_by_its_json_size() -> None:
    """Arabic text is six JSON characters per letter, as compaction counts it."""

    max_bytes = 64 * 1024
    service = AuditService(
        AuditRepository(None),
        ingestion_config=AuditIngestionConfig(enabled=True, payload_max_bytes=max_bytes),
    )
    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(audit_service=service)),
    )
    auth = SimpleNamespace(organization_id="org-1")

    async def build():
        reply = stream_reply_accumulator(request, auth)
        assert reply is not None
        for _ in range(200):
            reply.add_chunk(_chunk({"content": "مرحبا بالعالم " * 20}))
        reply.add_chunk(
            _chunk({"reasoning_details": [{"index": 0, "type": "reasoning.text", "text": "ع"}]})
        )
        return reply.completion(fallback_model="m", usage=None)

    body = asyncio.run(build())
    stored, changed = compact_audit_content(body, max_bytes=max_bytes)

    assert body["truncated"] is True
    assert changed is False
    content = stored["choices"][0]["message"]["content"]
    assert content.startswith("مرحبا")
    # Counted by length, the reply would have kept about six times more text.
    assert len(content) < max_bytes // 6


def test_bounded_replies_count_text_at_its_json_size() -> None:
    reply = StreamReplyAccumulator()
    reply.add_chunk(_chunk({"content": "ع" * 10}))
    assert reply.message()["content"] == "ع" * 10
    bounded = StreamReplyAccumulator(max_chars=1000)
    bounded.add_chunk(_chunk({"content": "ع" * 10}))
    assert bounded._chars == 60


class _Unreadable(list):
    def __iter__(self):
        raise AssertionError("the list must not be read")


class _UnreadableDelta(dict):
    def get(self, *args):
        raise AssertionError("a stopped reply must not read the delta")

    def items(self):
        raise AssertionError("a stopped reply must not read the delta")


def test_a_large_json_value_is_refused_without_encoding_it() -> None:
    # One reasoning frame with 35,000 numbers is refused by the value count.
    reply = StreamReplyAccumulator(max_chars=1_000_000)
    detail = {"index": 0, "type": "reasoning.encrypted", "points": [0.5] * 35_000}
    reply.add_chunk(_chunk({"reasoning_details": [detail]}))

    assert reply.truncated is True
    # The walk refuses a list over the bound before it reads the list.
    assert _json_size(_Unreadable([1] * 5000), max_chars=10**9, max_values=4096) is None


def test_a_stopped_reply_does_no_work_for_later_chunks() -> None:
    reply = StreamReplyAccumulator(max_chars=3)
    reply.add_chunk(_chunk({"content": "abcd"}))
    assert reply.truncated is True

    reply.add_delta(_UnreadableDelta(content="x"))
    reply.add_chunk(_chunk({"content": "y"}, finish="stop"))

    assert reply.finish_reason == "stop"


@pytest.mark.parametrize(
    "value",
    [
        {"a": [1, -20, 3.25e-300, None, True, False, 'x"y\\n', "ع😀"]},
        [{"k": {}}, [], [[]], 2**200, -(2**64), ""],
        {"ключ": {"inner": ["value", 0.1]}},
    ],
)
def test_measured_sizes_are_upper_bounds(value) -> None:
    size = _json_size(value, max_chars=10**6, max_values=4096)

    assert size is not None
    assert size[0] >= len(json.dumps(value, separators=(",", ":")))


def test_small_fragments_are_joined_and_their_heap_is_charged() -> None:
    memory = ReplyMemory(1 << 30)
    reply = StreamReplyAccumulator(max_chars=1 << 20, memory=memory)
    for _ in range(40_000):
        reply.add_chunk(_chunk({"content": "abc"}))

    parts = reply._content._parts
    kept = sum(sys.getsizeof(part) + 8 for part in parts)

    assert len(parts) <= 40_000 // 64 + 64
    assert kept <= memory.used
    # Without joins, the 120,000 characters would keep about 2 MB of string objects.
    assert memory.used < 2 * 120_000


def test_text_outside_ascii_is_charged_at_four_bytes_per_character() -> None:
    memory = ReplyMemory(1 << 30)
    reply = StreamReplyAccumulator(max_chars=1 << 20, memory=memory)
    reply.add_chunk(_chunk({"content": "a" * 1000}))
    reply.add_chunk(_chunk({"content": "😀"}))

    assert sys.getsizeof(reply._content.value()) <= memory.used


def test_completion_moves_the_data_and_keeps_the_charge_until_release() -> None:
    memory = ReplyMemory(1 << 20)
    reply = StreamReplyAccumulator(max_chars=1000, memory=memory)
    reply.add_chunk(_chunk({"content": "hello"}))
    held = memory.used

    body = reply.completion(fallback_model="x", usage=None)

    assert body["choices"][0]["message"]["content"] == "hello"
    assert reply._content.length == 0
    assert memory.used == held
    reply.add_chunk(_chunk({"content": "late"}))
    assert memory.used == held
    reply.release()
    assert memory.used == 0


def test_closing_the_stream_releases_the_reply_at_once() -> None:
    memory = ReplyMemory(1 << 20)
    reply = StreamReplyAccumulator(max_chars=1000, memory=memory)
    reply.add_chunk(_chunk({"content": "hello"}))
    released = []

    async def close_upstream(exc=None) -> None:
        del exc

    async def release_permit() -> None:
        released.append(True)

    async def disconnect() -> None:
        lifecycle = ManagedStreamLifecycle(
            SimpleNamespace(close=close_upstream),
            SimpleNamespace(release=release_permit),
            audit_reply=reply,
        )
        await lifecycle.close(asyncio.CancelledError())

    asyncio.run(disconnect())

    # No garbage collection is needed: the client disconnect path frees the share.
    assert memory.used == 0
    assert released == [True]


def test_completion_keeps_reasoning_and_the_legacy_function_call() -> None:
    reply = StreamReplyAccumulator(max_chars=1000)
    reply.add_chunk(_chunk({"reasoning": "step "}))
    reply.add_chunk(_chunk({"reasoning": "one"}))
    reply.add_chunk(_chunk({"function_call": {"name": "lookup", "arguments": ""}}))
    reply.add_chunk(_chunk({"function_call": {"arguments": '{"q":'}}))
    reply.add_chunk(_chunk({"function_call": {"arguments": '"x"}'}}, finish="function_call"))

    body = reply.completion(fallback_model="x", usage=None)

    assert body["choices"][0]["message"] == {
        "role": "assistant",
        "content": None,
        "reasoning": "step one",
        "function_call": {"name": "lookup", "arguments": '{"q":"x"}'},
    }
    assert "truncated" not in body


@pytest.mark.parametrize(
    "delta",
    [
        {"audio": {"data": "AAAA"}},
        {"content": [{"type": "text", "text": "a"}]},
        {"reasoning": {"text": "a"}},
        {"function_call": "lookup"},
        {"tool_calls": [["not", "an", "object"]]},
    ],
)
def test_data_that_is_not_rebuilt_marks_the_reply(delta) -> None:
    reply = StreamReplyAccumulator(max_chars=1000)
    reply.add_chunk(_chunk(delta))

    assert reply.completion(fallback_model="x", usage=None)["truncated"] is True


def test_empty_fields_and_logprobs() -> None:
    reply = StreamReplyAccumulator(max_chars=1000)
    reply.add_chunk(_chunk({"role": "assistant", "content": "", "audio": None, "annotations": []}))
    assert reply.truncated is False

    chunk = _chunk({"content": "a"})
    chunk["choices"][0]["logprobs"] = {"content": [{"token": "a", "logprob": -0.1}]}
    reply.add_chunk(chunk)

    assert reply.truncated is True


def test_tool_arguments_sent_as_an_object_are_charged_as_the_kept_string() -> None:
    memory = ReplyMemory(1 << 30)
    reply = StreamReplyAccumulator(max_chars=(1 << 20) - 16 * 1024, memory=memory)
    arguments = {"q": "\x00" * 130_000}
    reply.add_chunk(_chunk({"tool_calls": [{"index": 0, "function": {"arguments": arguments}}]}))

    kept = reply._tool_calls[0].arguments.value()

    assert kept == json.dumps(arguments, separators=(",", ":"))
    assert memory.used >= sys.getsizeof(kept)
    # The kept string counts at its own JSON size: each escape is escaped again.
    assert reply._chars == len(json.dumps(kept)) - 2 + _ENTRY_CHARS


def test_long_id_model_and_finish_reason_are_not_kept() -> None:
    memory = ReplyMemory(1 << 30)
    reply = StreamReplyAccumulator(max_chars=1000, memory=memory)
    chunk = _chunk({"content": "a"}, finish="s" * 300_000)
    chunk["id"] = "i" * 300_000
    chunk["model"] = "m" * 300_000
    reply.add_chunk(chunk)

    assert reply.response_id is None
    assert reply.model is None
    assert reply.finish_reason is None
    assert reply.truncated is True
    body = reply.completion(fallback_model="group", usage=None)
    assert body["model"] == "group"
    reply.release()
    assert memory.used == 0


def test_metadata_is_kept_when_short_and_cleared_on_release() -> None:
    reply = StreamReplyAccumulator(max_chars=1000, memory=ReplyMemory(1 << 20))
    reply.add_chunk(_chunk({"content": "a"}, finish="stop"))

    assert (reply.response_id, reply.model, reply.finish_reason) == ("chatcmpl-1", "m", "stop")
    reply.release()
    assert (reply.response_id, reply.model, reply.finish_reason) == (None, None, None)


def test_quotes_count_at_their_json_size_so_compaction_keeps_the_prefix() -> None:
    max_bytes = 64 * 1024
    service = AuditService(
        AuditRepository(None),
        ingestion_config=AuditIngestionConfig(enabled=True, payload_max_bytes=max_bytes),
    )
    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(audit_service=service)),
    )

    async def build():
        reply = stream_reply_accumulator(request, SimpleNamespace(organization_id="org-1"))
        assert reply is not None
        for _ in range(40):
            reply.add_chunk(_chunk({"content": '"' * 16_000}))
        reply.add_chunk(_chunk({"content": "ع"}))
        return reply.completion(fallback_model="m", usage=None)

    body = asyncio.run(build())
    stored, changed = compact_audit_content(body, max_bytes=max_bytes)

    assert body["truncated"] is True
    assert changed is False
    assert stored["choices"][0]["message"]["content"].startswith('"' * 16_000)


def test_large_provider_usage_keeps_only_the_token_counts() -> None:
    reply = StreamReplyAccumulator(max_chars=1000)
    reply.add_chunk(_chunk({"content": "a"}))
    usage = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3, "x": "y" * 10_000}

    body = reply.completion(fallback_model="m", usage=usage)

    assert body["usage"] == {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
    assert body["truncated"] is True
