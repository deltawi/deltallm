import json

from src.chat.mcp_stream_buffer import MCPStreamHopBuffer


def _line(delta, finish=None, chunk_id="c1"):
    return "data: " + json.dumps(
        {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
    )


def test_buffer_holds_tool_calls_and_merges_fragments() -> None:
    buffer = MCPStreamHopBuffer(completion_id=None)
    assert buffer.filter(_line({"content": "Hi"})) == _line({"content": "Hi"})
    assert (
        buffer.filter(
            _line(
                {
                    "tool_calls": [
                        {"index": 0, "id": "call_1", "function": {"name": "docs.", "arguments": ""}}
                    ]
                }
            )
        )
        is None
    )
    # Some providers repeat the full name on later deltas.
    buffer.filter(
        _line({"tool_calls": [{"index": 0, "function": {"name": "search", "arguments": '{"q":'}}]})
    )
    buffer.filter(
        _line(
            {"tool_calls": [{"index": 0, "function": {"name": "docs.search", "arguments": "1}"}}]}
        )
    )
    assert buffer.filter(_line({}, "tool_calls")) is None

    assert buffer.tool_calls() == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "docs.search", "arguments": '{"q":1}'},
        }
    ]
    message = buffer.assistant_response()["choices"][0]["message"]
    assert message["content"] == "Hi"


def test_finish_chunk_with_content_forwards_content_and_holds_finish() -> None:
    buffer = MCPStreamHopBuffer(completion_id=None)
    forwarded = json.loads(buffer.filter(_line({"content": "end"}, "stop"))[len("data: ") :])
    assert forwarded["choices"][0] == {
        "index": 0,
        "delta": {"content": "end"},
        "finish_reason": None,
    }
    held = [json.loads(line[len("data: ") :]) for line in buffer.client_lines()]
    assert held[-1]["choices"][0] == {"index": 0, "delta": {}, "finish_reason": "stop"}


def test_later_hop_uses_first_hop_completion_id() -> None:
    buffer = MCPStreamHopBuffer(completion_id="first")
    forwarded = json.loads(
        buffer.filter(_line({"content": "x"}, chunk_id="second"))[len("data: ") :]
    )
    assert forwarded["id"] == "first"
    assert json.loads(buffer.usage_line({"total_tokens": 3})[len("data: ") :]) == {
        "id": "first",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "m",
        "choices": [],
        "usage": {"total_tokens": 3},
    }


def test_reasoning_details_merge_by_index() -> None:
    buffer = MCPStreamHopBuffer(completion_id=None)
    buffer.filter(
        _line({"reasoning_details": [{"index": 0, "type": "reasoning.text", "text": "a"}]})
    )
    buffer.filter(_line({"reasoning_details": [{"index": 0, "text": "b", "signature": "sig"}]}))
    message = buffer.assistant_response()["choices"][0]["message"]
    assert message["reasoning_details"] == [
        {"index": 0, "type": "reasoning.text", "text": "ab", "signature": "sig"}
    ]
