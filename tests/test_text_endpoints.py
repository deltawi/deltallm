from __future__ import annotations

import json as jsonlib

import httpx
import pytest

from src.mcp.models import MCPToolCallResult
from src.models.requests import ResponsesRequest
from src.routers.text_adapters import responses_to_chat_request


class _AlwaysBudgetExceeded:
    async def check_budgets(self, **kwargs):
        del kwargs
        from src.billing.budget import BudgetExceeded
        raise BudgetExceeded(entity_type="key", entity_id="k1", spend=10.0, max_budget=5.0)


class _RecordingAuditService:
    def __init__(self) -> None:
        self.records: list[tuple[object, list[object], bool]] = []

    def record_event(self, event, *, payloads=None, critical=False):  # noqa: ANN001, ANN201
        self.records.append((event, list(payloads or []), critical))


class _FakeMCPGateway:
    def __init__(self) -> None:
        self.tool_calls: list[tuple[str, dict[str, object]]] = []

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
                    "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            )()
        ]

    async def call_tool(self, auth, *, namespaced_tool_name, arguments, request_headers=None, request_id=None, correlation_id=None):  # noqa: ANN001, ANN201
        del auth, request_headers, request_id, correlation_id
        self.tool_calls.append((namespaced_tool_name, dict(arguments or {})))
        return MCPToolCallResult(
            content=[{"type": "text", "text": "delta docs result"}],
            structured_content={"answer": "delta docs result"},
            is_error=False,
        )


@pytest.mark.asyncio
async def test_completions_success(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "prompt": "hello", "stream": False}

    response = await client.post("/v1/completions", headers=headers, json=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "text_completion"
    assert payload["choices"][0]["text"] == "ok"


@pytest.mark.asyncio
async def test_completions_stream_success(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "prompt": "hello", "stream": True}

    response = await client.post("/v1/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "data: [DONE]" in response.text
    assert '"object":"text_completion"' in response.text


@pytest.mark.asyncio
async def test_completions_unsupported_field_returns_400(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "prompt": "hello", "echo": True}

    response = await client.post("/v1/completions", headers=headers, json=body)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_responses_success(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "input": "hello", "stream": False}

    response = await client.post("/v1/responses", headers=headers, json=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "response"
    assert payload["output"][0]["content"][0]["text"] == "ok"


def test_responses_to_chat_request_preserves_mcp_tools() -> None:
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-4o-mini",
            "input": "hello",
            "tools": [{"type": "mcp", "server": "docs", "allowed_tools": ["search"], "require_approval": "never"}],
            "tool_choice": "auto",
        }
    )

    canonical = responses_to_chat_request(payload)

    assert canonical.tools is not None
    assert canonical.tools[0].type == "mcp"
    assert getattr(canonical.tools[0], "server", None) == "docs"
    assert canonical.tool_choice == "auto"


@pytest.mark.asyncio
async def test_responses_with_mcp_tool_auto_executes(client, test_app):
    gateway = _FakeMCPGateway()
    test_app.state.mcp_gateway_service = gateway
    upstream_calls: list[dict[str, object]] = []

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del headers, timeout
        upstream_calls.append(json)
        assert url.endswith("/chat/completions")
        if len(upstream_calls) == 1:
            payload = {
                "id": "chatcmpl-resp-tool-1",
                "object": "chat.completion",
                "created": 1700000000,
                "model": json["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_docs_search",
                                    "type": "function",
                                    "function": {
                                        "name": "docs.search",
                                        "arguments": jsonlib.dumps({"query": "delta"}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }
            return httpx.Response(200, json=payload)

        assert any(message.get("role") == "tool" for message in json["messages"])
        payload = {
            "id": "chatcmpl-resp-tool-2",
            "object": "chat.completion",
            "created": 1700000001,
            "model": json["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "input": "Search docs for DeltaLLM",
        "tools": [{"type": "mcp", "server": "docs", "allowed_tools": ["search"], "require_approval": "never"}],
    }

    response = await client.post("/v1/responses", headers=headers, json=body)

    assert response.status_code == 200
    assert response.json()["object"] == "response"
    assert response.json()["output"][0]["content"][0]["text"] == "done"
    assert len(upstream_calls) == 2
    assert gateway.tool_calls == [("docs.search", {"query": "delta"})]


@pytest.mark.asyncio
async def test_responses_stream_success(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "input": "hello", "stream": True}

    response = await client.post("/v1/responses", headers=headers, json=body)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "data: [DONE]" in response.text
    assert '"object":"response.output_text.delta"' in response.text


@pytest.mark.asyncio
async def test_responses_stream_with_mcp_tools_returns_400(client, test_app):
    test_app.state.mcp_gateway_service = _FakeMCPGateway()
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "input": "hello",
        "stream": True,
        "tools": [{"type": "mcp", "server": "docs", "allowed_tools": ["search"], "require_approval": "never"}],
    }

    response = await client.post("/v1/responses", headers=headers, json=body)

    assert response.status_code == 400
    assert "MCP tools are not supported on streaming chat requests yet" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1/chat/completions", {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]}),
        ("/v1/completions", {"model": "gpt-4o-mini", "prompt": "hello"}),
        ("/v1/responses", {"model": "gpt-4o-mini", "input": "hello"}),
    ],
)
async def test_text_endpoints_budget_exceeded_returns_429(client, test_app, path, body):
    test_app.state.budget_service = _AlwaysBudgetExceeded()
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    response = await client.post(path, headers=headers, json=body)
    assert response.status_code == 429
    error = response.json()["error"]
    assert error["type"] == "budget_exceeded"
    assert error["code"] == "budget_exceeded"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body", "expected_action"),
    [
        ("/v1/chat/completions", {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}, "CHAT_COMPLETION_REQUEST"),
        ("/v1/completions", {"model": "gpt-4o-mini", "prompt": "hello", "stream": False}, "COMPLETION_REQUEST"),
        ("/v1/responses", {"model": "gpt-4o-mini", "input": "hello", "stream": False}, "RESPONSES_REQUEST"),
        ("/v1/embeddings", {"model": "text-embedding-3-small", "input": "hello"}, "EMBEDDING_REQUEST"),
    ],
)
async def test_data_plane_routes_emit_audit_success(client, test_app, path, body, expected_action):
    audit = _RecordingAuditService()
    test_app.state.audit_service = audit

    headers = {"Authorization": f"Bearer {test_app.state._test_key}", "x-request-id": "req-audit-success"}
    response = await client.post(path, headers=headers, json=body)
    assert response.status_code == 200

    assert audit.records
    event, payloads, critical = audit.records[-1]
    assert event.action == expected_action
    assert event.status == "success"
    assert event.request_id == "req-audit-success"
    assert event.resource_id == body["model"]
    assert event.latency_ms >= 0
    assert critical is True
    assert payloads


@pytest.mark.asyncio
async def test_chat_stream_emits_audit_success(client, test_app):
    audit = _RecordingAuditService()
    test_app.state.audit_service = audit
    headers = {"Authorization": f"Bearer {test_app.state._test_key}", "x-request-id": "req-stream-audit"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": True}

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert "data: [DONE]" in response.text

    assert audit.records
    event, _, _ = audit.records[-1]
    assert event.action == "CHAT_COMPLETION_REQUEST"
    assert event.status == "success"
    assert event.request_id == "req-stream-audit"
