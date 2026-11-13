"""The stream reply accumulator rebuilds one assistant message from deltas."""

import asyncio
import gc
from types import SimpleNamespace

import pytest

from src.chat.audit import stream_reply_accumulator
from src.chat.stream_reply import ReplyMemory, StreamReplyAccumulator
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


def test_completion_omits_empty_tool_calls_and_ignores_other_choices() -> None:
    reply = StreamReplyAccumulator(max_chars=1000)
    reply.add_chunk(_chunk({"content": "kept"}, finish="stop"))
    reply.add_chunk(_chunk({"content": "dropped"}, index=1))

    body = reply.completion(fallback_model="x", usage=None)

    assert body["choices"][0]["message"] == {"role": "assistant", "content": "kept"}
    assert "usage" not in body


def test_limit_drops_later_text_and_marks_truncation() -> None:
    reply = StreamReplyAccumulator(max_chars=5)
    reply.add_chunk(_chunk({"content": "abc"}))
    reply.add_chunk(_chunk({"content": "defgh"}))
    reply.add_chunk(_chunk({"content": "i"}))
    reply.add_chunk(_chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}))

    body = reply.completion(fallback_model="x", usage=None)

    assert body["choices"][0]["message"]["content"] == "abc"
    assert body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert body["truncated"] is True


def test_limit_bounds_the_number_of_tool_calls() -> None:
    reply = StreamReplyAccumulator(max_chars=100_000)
    reply.add_chunk(
        _chunk({"tool_calls": [{"index": i, "function": {"name": "f"}} for i in range(200)]})
    )

    body = reply.completion(fallback_model="x", usage=None)

    assert len(body["choices"][0]["message"]["tool_calls"]) == 128
    assert body["truncated"] is True


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
    reply = StreamReplyAccumulator(max_chars=5)
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
    reply = StreamReplyAccumulator(max_chars=50)
    reply.add_delta({"reasoning_details": [{"index": 0, "type": "reasoning.text", "text": "a"}]})
    reply.add_delta({"reasoning_details": [{"index": 0, "extra": {"data": "x" * 100}}]})
    reply.add_delta({"reasoning_details": [{"index": 1, "nested": ["y" * 100]}]})

    details = reply.message()["reasoning_details"]

    assert details == [{"index": 0, "type": "reasoning.text", "text": "a"}]
    assert reply.truncated is True


def test_unhashable_reasoning_index_does_not_raise() -> None:
    reply = StreamReplyAccumulator(max_chars=100)
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
    memory = ReplyMemory(10)
    first = StreamReplyAccumulator(max_chars=100, memory=memory)
    second = StreamReplyAccumulator(max_chars=100, memory=memory)

    first.add_chunk(_chunk({"content": "abcdefgh"}))
    second.add_chunk(_chunk({"content": "abcdefgh"}))

    assert memory.used == 8
    assert not first.truncated
    assert second.truncated
    first.release()
    assert memory.used == 0
    # A released reply takes no more data.
    first.add_chunk(_chunk({"content": "x"}))
    assert memory.used == 0

    third = StreamReplyAccumulator(max_chars=100, memory=memory)
    third.add_chunk(_chunk({"content": "abc"}))
    assert memory.used == 3
    del third  # a failed or cancelled stream drops its accumulator
    gc.collect()
    assert memory.used == 0


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


def test_unbounded_replies_count_text_by_length() -> None:
    reply = StreamReplyAccumulator()
    reply.add_chunk(_chunk({"content": "ع" * 10}))
    assert reply._chars == 10
    bounded = StreamReplyAccumulator(max_chars=1000)
    bounded.add_chunk(_chunk({"content": "ع" * 10}))
    assert bounded._chars == 60
