from __future__ import annotations

import asyncio
import json as jsonlib

import httpx
import pytest

from src.audit.actions import AuditAction
from src.callbacks import CallbackManager, CustomLogger
from src.guardrails.base import CustomGuardrail, GuardrailAction
from src.guardrails.exceptions import GuardrailViolationError
from src.mcp.exceptions import MCPApprovalRequiredError
from src.mcp.exceptions import MCPRateLimitError
from src.mcp.exceptions import MCPToolTimeoutError
from src.mcp.exceptions import MCPTransportError
from src.mcp.models import MCPToolCallResult


class BlockingChatGuardrail(CustomGuardrail):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        del user_api_key_dict, cache, data, call_type
        raise GuardrailViolationError(self.name, "blocked by policy", "content_policy")


class LoggingChatGuardrail(BlockingChatGuardrail):
    def __init__(self, name: str):
        super().__init__(name=name, action=GuardrailAction.LOG)


class RecordingCallback(CustomLogger):
    def __init__(self):
        self.success = 0
        self.failure = 0

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        del kwargs, response_obj, start_time, end_time
        self.success += 1

    async def async_log_failure_event(self, kwargs, exception, start_time, end_time):
        del kwargs, exception, start_time, end_time
        self.failure += 1


class BlockingMCPToolGuardrail(CustomGuardrail):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        del user_api_key_dict, cache
        if call_type == "mcp_tool" and isinstance(data, dict) and data.get("tool_name") == "search":
            raise GuardrailViolationError(self.name, "blocked MCP tool", "content_policy")
        return data


class _RecordingAuditService:
    def __init__(self) -> None:
        self.records: list[tuple[object, list[object], bool]] = []

    def record_event(self, event, *, payloads=None, critical=False):  # noqa: ANN001, ANN201
        self.records.append((event, list(payloads or []), critical))


class _ExplodingMCPGateway:
    async def list_visible_tools(self, auth):  # noqa: ANN001, ANN201
        del auth
        raise AssertionError("MCP gateway should not be used for non-MCP requests")

    async def call_tool(self, auth, **kwargs):  # noqa: ANN001, ANN201
        del auth, kwargs
        raise AssertionError("MCP gateway should not be used for non-MCP requests")


class _FakeMCPGateway:
    def __init__(self) -> None:
        self.tool_calls: list[tuple[str, dict[str, object], str | None]] = []

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
                    "scope_type": "team",
                    "scope_id": "team-ops",
                },
            )()
        ]

    async def call_tool(self, auth, *, namespaced_tool_name, arguments, request_headers=None, request_id=None, correlation_id=None):  # noqa: ANN001, ANN201
        del auth
        del correlation_id
        self.tool_calls.append((namespaced_tool_name, dict(arguments or {}), request_id or (request_headers or {}).get("x-request-id")))
        return MCPToolCallResult(
            content=[{"type": "text", "text": "delta docs result"}],
            structured_content={"answer": "delta docs result"},
            is_error=False,
        )


class _FailingMCPGateway(_FakeMCPGateway):
    async def call_tool(self, auth, *, namespaced_tool_name, arguments, request_headers=None, request_id=None, correlation_id=None):  # noqa: ANN001, ANN201
        del auth, namespaced_tool_name, arguments, request_headers, request_id, correlation_id
        raise MCPTransportError("upstream MCP unavailable")


class _RateLimitedMCPGateway(_FakeMCPGateway):
    async def call_tool(self, auth, *, namespaced_tool_name, arguments, request_headers=None, request_id=None, correlation_id=None):  # noqa: ANN001, ANN201
        del auth, namespaced_tool_name, arguments, request_headers, request_id, correlation_id
        raise MCPRateLimitError("Rate limit exceeded for scope 'mcp_tool_rpm'", retry_after=42)


class _ApprovalRequiredMCPGateway(_FakeMCPGateway):
    async def call_tool(self, auth, *, namespaced_tool_name, arguments, request_headers=None, request_id=None, correlation_id=None):  # noqa: ANN001, ANN201
        del auth, namespaced_tool_name, arguments, request_headers, request_id, correlation_id
        raise MCPApprovalRequiredError("MCP tool 'docs.search' requires approval", approval_request_id="approval-1")


class _TimeoutMCPGateway(_FakeMCPGateway):
    async def call_tool(self, auth, *, namespaced_tool_name, arguments, request_headers=None, request_id=None, correlation_id=None):  # noqa: ANN001, ANN201
        del auth, namespaced_tool_name, arguments, request_headers, request_id, correlation_id
        raise MCPToolTimeoutError("MCP tool 'docs.search' exceeded the policy execution limit of 10 ms", timeout_ms=10)


@pytest.mark.asyncio
async def test_chat_completion_success(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert response.headers.get("x-deltallm-route-group") == "gpt-4o-mini"
    assert response.headers.get("x-deltallm-route-strategy") == "simple-shuffle"
    assert response.headers.get("x-deltallm-route-deployment")
    assert response.headers.get("x-deltallm-route-fallback-used") == "false"
    payload = response.json()
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"]["content"] == "ok"


@pytest.mark.asyncio
async def test_chat_completion_with_mcp_tool_auto_executes_and_audits(client, test_app):
    gateway = _FakeMCPGateway()
    audit = _RecordingAuditService()
    test_app.state.mcp_gateway_service = gateway
    test_app.state.audit_service = audit

    upstream_calls: list[dict[str, object]] = []

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del headers, timeout
        upstream_calls.append(json)
        assert url.endswith("/chat/completions")
        if len(upstream_calls) == 1:
            assert json["tools"][0]["function"]["name"] == "docs.search"
            payload = {
                "id": "chatcmpl-tool-1",
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
            "id": "chatcmpl-tool-2",
            "object": "chat.completion",
            "created": 1700000001,
            "model": json["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}", "x-request-id": "req-mcp-chat"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Search docs for DeltaLLM"}],
        "tools": [{"type": "mcp", "server": "docs", "allowed_tools": ["search"], "require_approval": "never"}],
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "done"
    assert len(upstream_calls) == 2
    assert gateway.tool_calls == [("docs.search", {"query": "delta"}, "req-mcp-chat")]
    assert any(getattr(event, "action", None) == AuditAction.MCP_TOOL_CALL.value for event, _, _ in audit.records)


@pytest.mark.asyncio
async def test_chat_completion_with_mcp_tool_guardrail_blocks_tool_execution(client, test_app):
    gateway = _FakeMCPGateway()
    test_app.state.mcp_gateway_service = gateway
    test_app.state.guardrail_registry.register(BlockingMCPToolGuardrail(name="block-mcp-tool", default_on=True))

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del url, headers, timeout
        payload = {
            "id": "chatcmpl-tool-block",
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
                                "function": {"name": "docs.search", "arguments": "{\"query\":\"delta\"}"},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Search docs for DeltaLLM"}],
        "tools": [{"type": "mcp", "server": "docs", "allowed_tools": ["search"], "require_approval": "never"}],
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "guardrail_violation"
    assert gateway.tool_calls == []


@pytest.mark.asyncio
async def test_chat_completion_with_mcp_tool_failure_emits_error_audit(client, test_app):
    gateway = _FailingMCPGateway()
    audit = _RecordingAuditService()
    test_app.state.mcp_gateway_service = gateway
    test_app.state.audit_service = audit

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del url, headers, timeout
        payload = {
            "id": "chatcmpl-tool-fail",
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
                                "function": {"name": "docs.search", "arguments": "{\"query\":\"delta\"}"},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}", "x-request-id": "req-mcp-tool-fail"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Search docs for DeltaLLM"}],
        "tools": [{"type": "mcp", "server": "docs", "allowed_tools": ["search"], "require_approval": "never"}],
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 503
    matching = [event for event, _, _ in audit.records if getattr(event, "action", None) == AuditAction.MCP_TOOL_CALL.value]
    assert matching
    assert matching[-1].status == "error"
    assert matching[-1].request_id == "req-mcp-tool-fail"
    assert matching[-1].metadata["scope_type"] == "team"
    assert matching[-1].metadata["scope_id"] == "team-ops"


@pytest.mark.asyncio
async def test_chat_completion_with_mcp_tool_rate_limit_returns_429(client, test_app):
    test_app.state.mcp_gateway_service = _RateLimitedMCPGateway()

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del url, headers, timeout
        payload = {
            "id": "chatcmpl-tool-rate-limit",
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
                                "function": {"name": "docs.search", "arguments": "{\"query\":\"delta\"}"},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Search docs for DeltaLLM"}],
        "tools": [{"type": "mcp", "server": "docs", "allowed_tools": ["search"], "require_approval": "never"}],
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 429
    assert response.json()["error"]["type"] == "rate_limit_error"


@pytest.mark.asyncio
async def test_chat_completion_with_mcp_tool_manual_approval_returns_409(client, test_app):
    test_app.state.mcp_gateway_service = _ApprovalRequiredMCPGateway()

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del url, headers, timeout
        payload = {
            "id": "chatcmpl-tool-approval",
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
                                "function": {"name": "docs.search", "arguments": "{\"query\":\"delta\"}"},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Search docs for DeltaLLM"}],
        "tools": [{"type": "mcp", "server": "docs", "allowed_tools": ["search"], "require_approval": "never"}],
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 409
    assert response.json()["error"]["type"] == "approval_required"
    assert response.json()["error"]["approval_request_id"] == "approval-1"


@pytest.mark.asyncio
async def test_chat_completion_with_mcp_tool_timeout_returns_503(client, test_app):
    test_app.state.mcp_gateway_service = _TimeoutMCPGateway()

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del url, headers, timeout
        payload = {
            "id": "chatcmpl-tool-timeout",
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
                                "function": {"name": "docs.search", "arguments": "{\"query\":\"delta\"}"},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Search docs for DeltaLLM"}],
        "tools": [{"type": "mcp", "server": "docs", "allowed_tools": ["search"], "require_approval": "never"}],
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "service_unavailable"


@pytest.mark.asyncio
async def test_chat_completion_without_mcp_tools_skips_mcp_gateway(client, test_app):
    test_app.state.mcp_gateway_service = _ExplodingMCPGateway()
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_chat_completion_streaming_success(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers.get("x-deltallm-route-group") == "gpt-4o-mini"
    assert response.headers.get("x-deltallm-route-strategy") == "simple-shuffle"
    assert "data: [DONE]" in response.text


@pytest.mark.asyncio
async def test_chat_completion_guardrail_blocks(client, test_app):
    test_app.state.guardrail_registry.register(BlockingChatGuardrail(name="block-chat", default_on=True))
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "guardrail_violation"
    assert payload["error"]["guardrail"] == "block-chat"


@pytest.mark.asyncio
async def test_chat_completion_guardrail_log_mode_allows_request(client, test_app):
    test_app.state.guardrail_registry.register(LoggingChatGuardrail(name="log-chat"))
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_chat_completion_runs_success_callback(client, test_app):
    recorder = RecordingCallback()
    manager = CallbackManager()
    manager.register_callback(recorder, callback_type="success")
    test_app.state.callback_manager = manager

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    await asyncio.sleep(0.05)
    assert recorder.success == 1


@pytest.mark.asyncio
async def test_chat_completion_runs_failure_callback(client, test_app):
    recorder = RecordingCallback()
    manager = CallbackManager()
    manager.register_callback(recorder, callback_type="failure")
    test_app.state.callback_manager = manager

    async def failing_post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        import httpx

        del headers, json, timeout
        return httpx.Response(503, json={"error": "unavailable"}, request=httpx.Request("POST", url))

    test_app.state.http_client.post = failing_post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 503
    await asyncio.sleep(0.05)
    assert recorder.failure == 1


class _StreamContext:
    def __init__(self, status_code: int, lines: list[str]) -> None:
        self.status_code = status_code
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


@pytest.mark.asyncio
async def test_stream_retries_before_first_token_with_failover(client, test_app):
    registry = test_app.state.router.deployment_registry["gpt-4o-mini"]
    registry.append(
        type(registry[0])(
            deployment_id="gpt-4o-mini-fallback",
            model_name="gpt-4o-mini",
            deltallm_params={"model": "openai/gpt-4o-mini", "api_key": "provider-key-fallback"},
            model_info={},
        )
    )

    async def choose_primary(model_group, request_context):  # noqa: ANN001, ANN201
        del request_context
        return test_app.state.router.deployment_registry[model_group][0]

    test_app.state.router.select_deployment = choose_primary

    calls = {"count": 0}

    def stream(method: str, url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001
        del method, url, json, timeout
        calls["count"] += 1
        auth = headers.get("Authorization", "")
        if auth.endswith("provider-key"):
            return _StreamContext(status_code=503, lines=[])
        return _StreamContext(
            status_code=200,
            lines=[
                'data: {"id":"chatcmpl-fb","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}',
                'data: [DONE]',
            ],
        )

    test_app.state.http_client.stream = stream
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": True}

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_chat_completion_rejects_unsupported_provider(client, test_app):
    registry = test_app.state.router.deployment_registry["gpt-4o-mini"]
    registry[0].deltallm_params["provider"] = "xai"

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 400
    payload = response.json()
    assert "Unsupported provider" in payload.get("error", {}).get("message", "")


@pytest.mark.asyncio
async def test_chat_completion_uses_azure_api_key_header_when_provider_is_azure(client, test_app):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "azure_openai"
    deployment.deltallm_params["api_base"] = "https://azure.example/openai/v1"
    deployment.deltallm_params["api_key"] = "azure-provider-key"

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del timeout
        assert url.endswith("/chat/completions")
        assert headers.get("api-key") == "azure-provider-key"
        assert "Authorization" not in headers
        payload = {
            "id": "chatcmpl-azure",
            "object": "chat.completion",
            "created": 1700000000,
            "model": json["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_chat_completion_does_not_forward_internal_metadata_upstream(client, test_app):
    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del url, headers, timeout
        assert "metadata" not in json
        payload = {
            "id": "chatcmpl-no-metadata",
            "object": "chat.completion",
            "created": 1700000000,
            "model": json["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
        "metadata": {"prompt_ref": {"template_key": "support.prompt", "label": "production"}},
    }
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_chat_completion_uses_gemini_native_endpoint(client, test_app):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "gemini"
    deployment.deltallm_params["model"] = "gemini/gemini-2.5-flash"
    deployment.deltallm_params["api_base"] = "https://generativelanguage.googleapis.com/v1beta"
    deployment.deltallm_params["api_key"] = "gemini-key"

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del timeout, headers
        assert "/models/gemini-2.5-flash:generateContent?key=gemini-key" in url
        assert "contents" in json
        payload = {
            "responseId": "resp_123",
            "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "ok"


@pytest.mark.asyncio
async def test_chat_completion_rejects_gemini_streaming_for_now(client, test_app):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "gemini"
    deployment.deltallm_params["model"] = "gemini/gemini-2.5-flash"
    deployment.deltallm_params["api_key"] = "gemini-key"

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": True}
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 400
    assert "not supported yet" in response.text


@pytest.mark.asyncio
async def test_chat_completion_uses_bedrock_sigv4_headers(client, test_app):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "bedrock"
    deployment.deltallm_params["model"] = "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
    deployment.deltallm_params["region"] = "us-east-1"
    deployment.deltallm_params["aws_access_key_id"] = "AKIDEXAMPLE"
    deployment.deltallm_params["aws_secret_access_key"] = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"

    async def post(url, headers, json=None, content=None, timeout=0):  # noqa: ANN001, ANN201
        del timeout, json
        assert "/model/anthropic.claude-3-5-sonnet-20240620-v1:0/converse" in url
        assert content is not None
        assert headers.get("Authorization", "").startswith("AWS4-HMAC-SHA256 ")
        assert headers.get("X-Amz-Date")
        assert headers.get("X-Amz-Content-Sha256")
        payload = {
            "requestId": "req_123",
            "output": {"message": {"content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "ok"
