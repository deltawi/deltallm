from __future__ import annotations

import json as jsonlib

import httpx
import pytest

from src.mcp.models import MCPToolCallResult
from src.models.requests import ResponsesRequest
from src.routers.responses_request_view import build_responses_policy_view


class _AlwaysBudgetExceeded:
    async def check_budgets(self, **kwargs):
        del kwargs
        from src.billing.budgets.budget import BudgetExceeded

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

    async def call_tool(
        self,
        auth,
        *,
        namespaced_tool_name,
        arguments,
        request_headers=None,
        request_id=None,
        correlation_id=None,
    ):  # noqa: ANN001, ANN201
        del auth, request_headers, request_id, correlation_id
        self.tool_calls.append((namespaced_tool_name, dict(arguments or {})))
        return MCPToolCallResult(
            content=[{"type": "text", "text": "delta docs result"}],
            structured_content={"answer": "delta docs result"},
            is_error=False,
        )

    async def tool_requires_manual_approval(self, auth, *, server_key, tool_name):  # noqa: ANN001, ANN201
        del auth, server_key, tool_name
        return False


class _ManualApprovalMCPGateway(_FakeMCPGateway):
    async def tool_requires_manual_approval(self, auth, *, server_key, tool_name):  # noqa: ANN001, ANN201
        del auth, server_key, tool_name
        return True


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


CODEX_TOOL_HISTORY = {
    "model": "gpt-4o-mini",
    "stream": False,
    "store": False,
    "include": ["reasoning.encrypted_content"],
    "reasoning": {"summary": "auto"},
    "metadata": {"tenant": "internal"},
    "tools": [
        {"type": "function", "name": "exec_command", "parameters": {"type": "object"}},
        {
            "type": "namespace",
            "name": "multi_agent_v1",
            "tools": [{"type": "function", "name": "spawn", "parameters": {"type": "object"}}],
        },
    ],
    "input": [
        {"type": "message", "role": "developer", "content": "Be brief."},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "ls"}]},
        {"type": "reasoning", "summary": [], "encrypted_content": "opaque"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "exec_command",
            "arguments": '{"cmd": "ls"}',
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "README.md"},
    ],
}


async def _post_recorded_responses(client, test_app, monkeypatch, api_base: str | None):  # noqa: ANN001, ANN202
    registry_store = test_app.state.router.deployment_registry
    deployment = registry_store["gpt-4o-mini"][0]
    params = {**deployment.deltallm_params}
    if api_base is not None:
        params["api_base"] = api_base
    registry_store.replace(
        {
            **registry_store.snapshot(),
            "gpt-4o-mini": [
                type(deployment)(
                    deployment_id=deployment.deployment_id,
                    model_name=deployment.model_name,
                    deltallm_params=params,
                    model_info=dict(deployment.model_info),
                )
            ],
        }
    )
    sent: list[tuple[str, dict]] = []
    http_client = test_app.state.http_client
    original_post = http_client.post

    async def recording_post(url, headers, json, timeout):  # noqa: ANN001, ANN202
        sent.append((url, json))
        return await original_post(url, headers=headers, json=json, timeout=timeout)

    monkeypatch.setattr(http_client, "post", recording_post)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    response = await client.post("/v1/responses", headers=headers, json=CODEX_TOOL_HISTORY)
    return response, sent


@pytest.mark.asyncio
async def test_responses_native_hop_forwards_codex_tool_history(client, test_app, monkeypatch):
    response, sent = await _post_recorded_responses(client, test_app, monkeypatch, None)

    assert response.status_code == 200, response.text
    assert response.json()["object"] == "response"
    [(url, upstream)] = sent
    assert url.endswith("/responses")
    # Only model, stream and the DeltaLLM-owned metadata differ from the client body.
    unchanged = {"model", "stream", "metadata"}
    assert {key: value for key, value in upstream.items() if key not in unchanged} == {
        key: value for key, value in CODEX_TOOL_HISTORY.items() if key not in unchanged
    }
    assert "metadata" not in upstream


@pytest.mark.asyncio
async def test_responses_translation_hop_sends_tool_history_as_chat(client, test_app, monkeypatch):
    response, sent = await _post_recorded_responses(
        client, test_app, monkeypatch, "https://chat-only.example/v1"
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["object"] == "response"
    assert payload["output"][-1]["content"][0]["text"] == "ok"
    [(url, upstream)] = sent
    assert url.endswith("/chat/completions")
    assert [message["role"] for message in upstream["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert upstream["messages"][2]["tool_calls"][0]["function"]["name"] == "exec_command"
    assert upstream["messages"][3]["tool_call_id"] == "call_1"
    assert [tool["function"]["name"] for tool in upstream["tools"]] == [
        "exec_command",
        "multi_agent_v1__spawn",
    ]


def test_responses_policy_view_preserves_mcp_tools() -> None:
    body = {
        "model": "gpt-4o-mini",
        "input": "hello",
        "tools": [
            {
                "type": "mcp",
                "server": "docs",
                "allowed_tools": ["search"],
                "require_approval": "never",
            }
        ],
        "tool_choice": "auto",
    }

    view = build_responses_policy_view(ResponsesRequest.model_validate(body), body)
    canonical = view.canonical

    assert canonical.tools is not None
    assert canonical.tools[0].type == "mcp"
    assert getattr(canonical.tools[0], "server", None) == "docs"
    assert canonical.tool_choice == "auto"
    # Gateway MCP tools run in the canonical chat loop, so no native hop is offered.
    assert view.native_request(canonical) is None


@pytest.mark.asyncio
async def test_responses_with_mcp_tool_auto_executes(client, test_app):
    gateway = _FakeMCPGateway()
    test_app.state.mcp_gateway_service = gateway
    test_app.state.audit_service = _RecordingAuditService()
    registry_store = test_app.state.router.deployment_registry
    registry = list(registry_store["gpt-4o-mini"])
    primary = registry[0]
    fallback = type(primary)(
        deployment_id="gpt-4o-mini-responses-fallback",
        model_name=primary.model_name,
        deltallm_params={
            **primary.deltallm_params,
            "api_key": "provider-key-fallback",
        },
        model_info=dict(primary.model_info),
    )
    registry.append(fallback)
    registry_store.replace({**registry_store.snapshot(), "gpt-4o-mini": registry})

    async def choose_primary(model_group, request_context):  # noqa: ANN001, ANN201
        del model_group, request_context
        return primary

    test_app.state.router.select_deployment = choose_primary
    upstream_calls: list[dict[str, object]] = []
    attempted_auths: list[str | None] = []

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del timeout
        upstream_calls.append(json)
        attempted_auths.append(headers.get("Authorization"))
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
        if headers.get("Authorization") == "Bearer provider-key":
            return httpx.Response(
                503,
                json={"error": {"message": "follow-up unavailable"}},
                request=httpx.Request("POST", url),
            )
        payload = {
            "id": "chatcmpl-resp-tool-2",
            "object": "chat.completion",
            "created": 1700000001,
            "model": json["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "done"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "input": "Search docs for DeltaLLM",
        "tools": [
            {
                "type": "mcp",
                "server": "docs",
                "allowed_tools": ["search"],
                "require_approval": "never",
            }
        ],
    }

    response = await client.post("/v1/responses", headers=headers, json=body)

    assert response.status_code == 200
    assert response.json()["object"] == "response"
    assert response.json()["output"][0]["content"][0]["text"] == "done"
    assert len(upstream_calls) == 3
    assert attempted_auths == [
        "Bearer provider-key",
        "Bearer provider-key",
        "Bearer provider-key-fallback",
    ]
    assert response.headers["x-deltallm-route-deployment"] == fallback.deployment_id
    assert response.headers["x-deltallm-route-fallback-used"] == "true"
    assert gateway.tool_calls == [("docs.search", {"query": "delta"})]
    # Both model hops are billed: 5 tokens for the tool call turn, 8 for the answer.
    assert response.json()["usage"]["total_tokens"] == 13


@pytest.mark.asyncio
async def test_responses_stream_success(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "input": "hello", "stream": True}

    response = await client.post("/v1/responses", headers=headers, json=body)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [
        jsonlib.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["type"] for event in events][0] == "response.created"
    assert events[-1]["type"] == "response.completed"
    assert events[-1]["response"]["output"][0]["content"][0]["text"] == "ok"
    assert "data: [DONE]" not in response.text


@pytest.mark.asyncio
async def test_responses_with_manual_approval_mcp_tools_returns_400(client, test_app):
    test_app.state.mcp_gateway_service = _ManualApprovalMCPGateway()
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "input": "Search docs for DeltaLLM",
        "tools": [
            {
                "type": "mcp",
                "server": "docs",
                "allowed_tools": ["search"],
                "require_approval": "never",
            }
        ],
    }

    response = await client.post("/v1/responses", headers=headers, json=body)

    assert response.status_code == 400
    assert "manual approval" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/v1/chat/completions",
            {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
        ),
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
        (
            "/v1/chat/completions",
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
            "CHAT_COMPLETION_REQUEST",
        ),
        (
            "/v1/completions",
            {"model": "gpt-4o-mini", "prompt": "hello", "stream": False},
            "COMPLETION_REQUEST",
        ),
        (
            "/v1/responses",
            {"model": "gpt-4o-mini", "input": "hello", "stream": False},
            "RESPONSES_REQUEST",
        ),
        (
            "/v1/embeddings",
            {"model": "text-embedding-3-small", "input": "hello"},
            "EMBEDDING_REQUEST",
        ),
    ],
)
async def test_data_plane_routes_emit_audit_success(client, test_app, path, body, expected_action):
    audit = _RecordingAuditService()
    test_app.state.audit_service = audit

    headers = {
        "Authorization": f"Bearer {test_app.state._test_key}",
        "x-request-id": "req-audit-success",
    }
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
    headers = {
        "Authorization": f"Bearer {test_app.state._test_key}",
        "x-request-id": "req-stream-audit",
    }
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert "data: [DONE]" in response.text

    assert audit.records
    event, _, _ = audit.records[-1]
    assert event.action == "CHAT_COMPLETION_REQUEST"
    assert event.status == "success"
    assert event.request_id == "req-stream-audit"
