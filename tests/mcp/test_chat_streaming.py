from __future__ import annotations

import json
from typing import Any

import pytest

from src.mcp.exceptions import MCPAccessDeniedError
from src.mcp.models import MCPToolCallResult
from tests.mcp.test_chat_execution import _RecordingAuditService

pytestmark = pytest.mark.app

MCP_TOOL = {
    "type": "mcp",
    "server": "docs",
    "allowed_tools": ["search"],
    "require_approval": "never",
}
CLIENT_TOOL = {
    "type": "function",
    "function": {"name": "open_ticket", "parameters": {"type": "object"}},
}


class _Gateway:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.tool_calls: list[tuple[str, dict[str, Any]]] = []
        self.error = error

    async def list_visible_tools(self, auth):  # noqa: ANN001, ANN201
        del auth
        return [
            type(
                "VisibleTool",
                (),
                {
                    "server_key": "docs",
                    "original_name": "search",
                    "namespaced_name": "docs.search",
                    "description": "Search docs",
                    "input_schema": {"type": "object"},
                },
            )()
        ]

    async def tool_requires_manual_approval(self, auth, *, server_key, tool_name):  # noqa: ANN001, ANN201
        del auth, server_key, tool_name
        return False

    async def call_tool(self, auth, *, namespaced_tool_name, arguments, **kwargs):  # noqa: ANN001, ANN003, ANN201
        del auth, kwargs
        self.tool_calls.append((namespaced_tool_name, dict(arguments or {})))
        if self.error is not None:
            raise self.error
        return MCPToolCallResult(
            content=[{"type": "text", "text": "DeltaLLM is a gateway"}],
            is_error=False,
        )


def _chunk(
    chunk_id: str, delta: dict[str, Any] | None, finish: str | None = None, **extra: Any
) -> str:
    body: dict[str, Any] = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "gpt-4o-mini",
        "choices": [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}],
        **extra,
    }
    return "data: " + json.dumps(body)


def _tool_call_hop(name: str = "docs.search") -> list[str]:
    return [
        _chunk("hop-1", {"role": "assistant", "content": "Let me search. "}),
        _chunk(
            "hop-1",
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": name, "arguments": '{"que'},
                    }
                ]
            },
        ),
        _chunk("hop-1", {"tool_calls": [{"index": 0, "function": {"arguments": 'ry":"delta"}'}}]}),
        _chunk("hop-1", {}, "tool_calls"),
        _chunk(
            "hop-1",
            None,
            usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        ),
        "data: [DONE]",
    ]


def _answer_hop() -> list[str]:
    return [
        _chunk("hop-2", {"role": "assistant", "content": "It is "}),
        _chunk("hop-2", {"content": "a gateway."}),
        _chunk("hop-2", {}, "stop"),
        _chunk(
            "hop-2",
            None,
            usage={"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
        ),
        "data: [DONE]",
    ]


class _ScriptedStream:
    def __init__(self, lines: list[str]) -> None:
        self.status_code = 200
        self._lines = lines

    async def __aenter__(self):  # noqa: ANN204
        return self

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN204
        return None

    async def aiter_lines(self):  # noqa: ANN201
        for line in self._lines:
            yield line


def _script(test_app, hops: list[list[str]]) -> list[dict[str, Any]]:  # noqa: ANN001
    requests: list[dict[str, Any]] = []

    def stream(method, url, headers, json, timeout):  # noqa: ANN001, ANN202
        del method, url, headers, timeout
        requests.append(json)
        return _ScriptedStream(hops[len(requests) - 1])

    test_app.state.http_client.stream = stream
    return requests


def _events(text: str) -> list[str]:
    return [frame for frame in text.split("\n\n") if frame]


def _data(events: list[str]) -> list[dict[str, Any]]:
    return [
        json.loads(event[len("data: ") :])
        for event in events
        if event.startswith("data: ") and event != "data: [DONE]"
    ]


def _headers(test_app) -> dict[str, str]:  # noqa: ANN001
    return {"Authorization": f"Bearer {test_app.state._test_key}"}


async def test_chat_stream_runs_mcp_tool_and_streams_both_hops(client, test_app) -> None:
    gateway = _Gateway()
    test_app.state.mcp_gateway_service = gateway
    test_app.state.audit_service = _RecordingAuditService()
    upstream = _script(test_app, [_tool_call_hop(), _answer_hop()])

    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(test_app),
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "What is DeltaLLM?"}],
            "stream": True,
            "stream_options": {"include_usage": True},
            "tools": [MCP_TOOL],
        },
    )

    assert response.status_code == 200, response.text
    events = _events(response.text)
    chunks = _data(events)
    text = "".join(
        choice["delta"].get("content") or "" for chunk in chunks for choice in chunk["choices"]
    )
    assert text == "Let me search. It is a gateway."
    assert all(
        "tool_calls" not in choice["delta"] for chunk in chunks for choice in chunk["choices"]
    )
    assert {chunk["id"] for chunk in chunks} == {"hop-1"}
    finishes = [
        choice["finish_reason"]
        for chunk in chunks
        for choice in chunk["choices"]
        if choice["finish_reason"]
    ]
    assert finishes == ["stop"]
    usage_chunks = [chunk for chunk in chunks if chunk.get("usage")]
    assert len(usage_chunks) == 1
    assert usage_chunks[0]["usage"]["total_tokens"] == 39
    assert usage_chunks[0]["usage"]["prompt_tokens"] == 30
    assert ": mcp_tool_call docs.search" in events
    assert events[-1] == "data: [DONE]"
    assert gateway.tool_calls == [("docs.search", {"query": "delta"})]

    follow_up = upstream[1]["messages"]
    assistant = follow_up[-2]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == "Let me search. "
    assert assistant["tool_calls"][0]["function"] == {
        "name": "docs.search",
        "arguments": '{"query":"delta"}',
    }
    assert follow_up[-1] == {
        "role": "tool",
        "content": "DeltaLLM is a gateway",
        "tool_call_id": "call_1",
    }
    assert upstream[0]["tools"][0]["function"]["name"] == "docs.search"


async def test_chat_stream_without_usage_option_sends_no_usage_chunk(client, test_app) -> None:
    test_app.state.mcp_gateway_service = _Gateway()
    test_app.state.audit_service = _RecordingAuditService()
    _script(test_app, [_tool_call_hop(), _answer_hop()])

    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(test_app),
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "What is DeltaLLM?"}],
            "stream": True,
            "tools": [MCP_TOOL],
        },
    )

    assert response.status_code == 200, response.text
    assert all("usage" not in chunk for chunk in _data(_events(response.text)))


async def test_chat_stream_returns_client_tool_calls_to_client(client, test_app) -> None:
    gateway = _Gateway()
    test_app.state.mcp_gateway_service = gateway
    upstream = _script(test_app, [_tool_call_hop(name="open_ticket")])

    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(test_app),
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Open a ticket"}],
            "stream": True,
            "tools": [MCP_TOOL, CLIENT_TOOL],
        },
    )

    assert response.status_code == 200, response.text
    chunks = _data(_events(response.text))
    tool_chunks = [
        choice["delta"]["tool_calls"]
        for chunk in chunks
        for choice in chunk["choices"]
        if "tool_calls" in choice["delta"]
    ]
    assert tool_chunks == [
        [
            {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": "open_ticket", "arguments": '{"query":"delta"}'},
            }
        ]
    ]
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert response.text.rstrip().endswith("data: [DONE]")
    assert gateway.tool_calls == []
    assert len(upstream) == 1


async def test_responses_stream_runs_mcp_tool(client, test_app) -> None:
    gateway = _Gateway()
    test_app.state.mcp_gateway_service = gateway
    test_app.state.audit_service = _RecordingAuditService()
    _script(test_app, [_tool_call_hop(), _answer_hop()])

    response = await client.post(
        "/v1/responses",
        headers=_headers(test_app),
        json={
            "model": "gpt-4o-mini",
            "input": "What is DeltaLLM?",
            "stream": True,
            "tools": [MCP_TOOL],
        },
    )

    assert response.status_code == 200, response.text
    deltas = [
        event["delta"]
        for event in _data(_events(response.text))
        if event.get("object") == "response.output_text.delta"
    ]
    assert "".join(deltas) == "Let me search. It is a gateway."
    assert gateway.tool_calls == [("docs.search", {"query": "delta"})]


async def test_chat_stream_tool_failure_ends_stream_with_error(client, test_app) -> None:
    gateway = _Gateway(error=MCPAccessDeniedError("MCP tool 'docs.search' is not allowed"))
    test_app.state.mcp_gateway_service = gateway
    test_app.state.audit_service = _RecordingAuditService()
    upstream = _script(test_app, [_tool_call_hop()])

    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(test_app),
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "What is DeltaLLM?"}],
            "stream": True,
            "tools": [MCP_TOOL],
        },
    )

    assert response.status_code == 200
    events = _events(response.text)
    assert "data: [DONE]" not in events
    error = json.loads(events[-1][len("data: ") :])["error"]
    assert error["type"] == "permission_denied"
    assert "not allowed" in error["message"]
    assert len(upstream) == 1


async def test_chat_stream_with_mcp_tools_rejects_multiple_choices(client, test_app) -> None:
    test_app.state.mcp_gateway_service = _Gateway()
    upstream = _script(test_app, [_tool_call_hop()])

    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(test_app),
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "What is DeltaLLM?"}],
            "stream": True,
            "n": 2,
            "tools": [MCP_TOOL],
        },
    )

    assert response.status_code == 400
    assert "n=1" in response.json()["error"]["message"]
    assert upstream == []


async def test_chat_stream_stops_at_hop_limit(client, test_app) -> None:
    gateway = _Gateway()
    test_app.state.mcp_gateway_service = gateway
    test_app.state.audit_service = _RecordingAuditService()
    upstream = _script(test_app, [_tool_call_hop() for _ in range(6)])

    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(test_app),
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Loop"}],
            "stream": True,
            "tools": [MCP_TOOL],
        },
    )

    events = _events(response.text)
    assert "hop limit" in json.loads(events[-1][len("data: ") :])["error"]["message"]
    assert len(upstream) == 5
    assert len(gateway.tool_calls) == 4
