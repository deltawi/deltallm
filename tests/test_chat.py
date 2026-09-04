from __future__ import annotations

import asyncio
import json as jsonlib
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest

import src.routers.chat as chat_router
from src.audit.actions import AuditAction
from src.callbacks import CallbackManager, CustomLogger
from src.guardrails.base import CustomGuardrail, GuardrailAction
from src.guardrails.exceptions import GuardrailViolationError
from src.mcp.exceptions import MCPRateLimitError
from src.mcp.exceptions import MCPToolTimeoutError
from src.mcp.exceptions import MCPTransportError
from src.mcp.models import MCPToolCallResult
from src.models.errors import ServiceUnavailableError
from src.rate_limit_policy import estimate_tokens


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
        self.success_payloads: list[dict] = []
        self.failure_payloads: list[dict] = []

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        del response_obj, start_time, end_time
        self.success += 1
        self.success_payloads.append(dict(kwargs))

    async def async_log_failure_event(self, kwargs, exception, start_time, end_time):
        del exception, start_time, end_time
        self.failure += 1
        self.failure_payloads.append(dict(kwargs))


class RewritingPreCallCallback(CustomLogger):
    def __init__(
        self,
        *,
        model: str | None = None,
        system_message: str | None = None,
    ) -> None:
        self.model = model
        self.system_message = system_message
        self.calls = 0

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):  # noqa: ANN001
        del user_api_key_dict, cache
        assert call_type == "completion"
        self.calls += 1
        updated = dict(data)
        if self.model is not None:
            updated["model"] = self.model
        if self.system_message is not None:
            messages = list(updated.get("messages") or [])
            updated["messages"] = [
                {"role": "system", "content": self.system_message},
                *messages,
            ]
        return updated


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


class _FailingBestEffortAuditService:
    async def enqueue_event(self, *_args: Any, **kwargs: Any) -> str:
        if str(kwargs.get("delivery_class")) == "best_effort":
            raise ConnectionError("audit database unavailable")
        return "persisted"


class _FailingRequiredAuditService:
    async def enqueue_event(self, *_args: Any, **kwargs: Any) -> str:
        if str(getattr(kwargs.get("delivery_class"), "value", kwargs.get("delivery_class"))) == (
            "required"
        ):
            raise ConnectionError("required audit database unavailable")
        return "persisted"


class _SpendRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self._events_changed = asyncio.Condition()

    async def log_spend(self, **kwargs):
        async with self._events_changed:
            self.events.append({"status": "success", **kwargs})
            self._events_changed.notify_all()

    async def log_request_failure(self, **kwargs):
        error_type = kwargs.get("error_type")
        if error_type is None:
            exc = kwargs.get("exc")
            error_type = getattr(exc, "error_type", None) or (
                exc.__class__.__name__ if exc is not None else None
            )
        async with self._events_changed:
            self.events.append({"status": "error", "cost": 0.0, "error_type": error_type, **kwargs})
            self._events_changed.notify_all()

    async def wait_for_events(self, count: int) -> None:
        async with self._events_changed:
            await self._events_changed.wait_for(lambda: len(self.events) >= count)


class _TierPricingService:
    def __init__(self, pricing: dict[str, float], *, snapshot_stale: bool = False) -> None:
        self.pricing = pricing
        self.mode = "enforce"
        self.snapshot_stale = snapshot_stale

    def get_pricing_policy(self, organization_id: str, callable_key: str, *, mode: str = "sync"):
        if organization_id != "org-default" or callable_key != "gpt-4o-mini" or mode != "sync":
            return None
        return SimpleNamespace(
            mode="sync",
            pricing=self.pricing,
            source=SimpleNamespace(
                assignment_id="assignment-stream",
                tier_key="enterprise",
                tier_version_id="version-stream",
                tier_version_number=1,
                model_policy_id="policy-stream",
            ),
        )

    def get_snapshot(self):
        return SimpleNamespace(org_tier_keys={"org-default": ("enterprise",)})

    def resolve_unavailable_decision(self, organization_id: str):
        return SimpleNamespace(
            allowed=True,
            reason="tier_policy_unavailable_fail_open",
            explicit_tier_policy=organization_id == "org-default",
        )


class _ExplodingMCPGateway:
    async def list_visible_tools(self, auth):  # noqa: ANN001, ANN201
        del auth
        raise AssertionError("MCP gateway should not be used for non-MCP requests")

    async def call_tool(self, auth, **kwargs):  # noqa: ANN001, ANN201
        del auth, kwargs
        raise AssertionError("MCP gateway should not be used for non-MCP requests")


class _RaisingMCPOrchestrator:
    def __init__(self, gateway, audit_service=None, max_mcp_tool_hops=4, max_mcp_tools_per_turn=8):
        del gateway, audit_service, max_mcp_tool_hops, max_mcp_tools_per_turn

    async def execute(
        self, request_context, auth, payload, execute_chat_call, guardrail_middleware
    ):  # noqa: ANN001, ANN002, ANN003, ANN204
        del request_context, auth, payload, execute_chat_call, guardrail_middleware
        raise RuntimeError("unexpected orchestration failure")


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
        del auth
        del correlation_id
        self.tool_calls.append(
            (
                namespaced_tool_name,
                dict(arguments or {}),
                request_id or (request_headers or {}).get("x-request-id"),
            )
        )
        return MCPToolCallResult(
            content=[{"type": "text", "text": "delta docs result"}],
            structured_content={"answer": "delta docs result"},
            is_error=False,
        )

    async def tool_requires_manual_approval(self, auth, *, server_key, tool_name):  # noqa: ANN001, ANN201
        del auth, server_key, tool_name
        return False


class _FailingMCPGateway(_FakeMCPGateway):
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
        del auth, namespaced_tool_name, arguments, request_headers, request_id, correlation_id
        raise MCPTransportError("upstream MCP unavailable")


class _RateLimitedMCPGateway(_FakeMCPGateway):
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
        del auth, namespaced_tool_name, arguments, request_headers, request_id, correlation_id
        raise MCPRateLimitError("Rate limit exceeded for scope 'mcp_tool_rpm'", retry_after=42)


class _ManualApprovalMCPGateway(_FakeMCPGateway):
    async def tool_requires_manual_approval(self, auth, *, server_key, tool_name):  # noqa: ANN001, ANN201
        del auth, server_key, tool_name
        return True


class _TimeoutMCPGateway(_FakeMCPGateway):
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
        del auth, namespaced_tool_name, arguments, request_headers, request_id, correlation_id
        raise MCPToolTimeoutError(
            "MCP tool 'docs.search' exceeded the policy execution limit of 10 ms", timeout_ms=10
        )


class _BuggyMCPGateway(_FakeMCPGateway):
    async def list_visible_tools(self, auth):  # noqa: ANN001, ANN201
        del auth
        raise RuntimeError("local MCP bug")


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
    deployment_id = str(response.headers["x-deltallm-route-deployment"])
    payload = response.json()
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"]["content"] == "ok"
    usage = await test_app.state.router_state_backend.get_usage(deployment_id)
    assert usage == {"rpm": 1, "tpm": 2}
    latency = await test_app.state.router_state_backend.get_latency_window(deployment_id, 300_000)
    assert len(latency) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "include_assistant_content",
    [pytest.param(False, id="omitted"), pytest.param(True, id="null")],
)
async def test_chat_completion_preserves_assistant_tool_call_content_presence(
    client,
    test_app,
    include_assistant_content: bool,
) -> None:
    captured: dict[str, Any] = {}

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del url, headers, timeout
        captured.update(json)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-tool-history",
                "object": "chat.completion",
                "created": 1700000000,
                "model": json["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
            },
        )

    test_app.state.http_client.post = post
    assistant: dict[str, object] = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "search", "arguments": "{}"},
            }
        ],
    }
    if include_assistant_content:
        assistant["content"] = None

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "search"},
                assistant,
                {"role": "tool", "tool_call_id": "call_1", "content": "result"},
            ],
            "stream": False,
        },
    )

    assert response.status_code == 200
    forwarded_assistant = captured["messages"][1]
    assert ("content" in forwarded_assistant) is include_assistant_content
    if include_assistant_content:
        assert forwarded_assistant["content"] is None
    assert forwarded_assistant["tool_calls"] == assistant["tool_calls"]


@pytest.mark.asyncio
async def test_chat_authorizes_the_model_after_pre_call_transformation(client, test_app):
    rewriter = RewritingPreCallCallback(model="forbidden-model")
    manager = CallbackManager()
    manager.register_callback(rewriter, callback_type="success")
    test_app.state.callback_manager = manager
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}

    response = await client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["type"] == "permission_denied"
    assert "forbidden-model" in response.json()["error"]["message"]
    assert rewriter.calls == 1
    assert test_app.state.http_client.post_calls == 0


@pytest.mark.asyncio
async def test_chat_rate_limit_estimate_uses_final_transformed_payload(
    client,
    test_app,
    monkeypatch,
):
    import src.middleware.rate_limit as rate_limit_middleware

    system_message = "policy context " * 80
    rewriter = RewritingPreCallCallback(system_message=system_message)
    manager = CallbackManager()
    manager.register_callback(rewriter, callback_type="success")
    test_app.state.callback_manager = manager
    captured_tokens: list[int] = []
    original_acquire = rate_limit_middleware.acquire_rate_limit_controls

    async def capture_acquire(**kwargs):  # noqa: ANN003, ANN202
        captured_tokens.append(int(kwargs["tokens"]))
        return await original_acquire(**kwargs)

    monkeypatch.setattr(rate_limit_middleware, "acquire_rate_limit_controls", capture_acquire)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }
    final_body = {
        **body,
        "messages": [
            {"role": "system", "content": system_message},
            *body["messages"],
        ],
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200
    assert captured_tokens == [estimate_tokens(final_body)]
    assert captured_tokens[0] > estimate_tokens(body)
    assert rewriter.calls == 1


@pytest.mark.asyncio
async def test_chat_completion_uses_global_read_timeout_without_deployment_override(
    client, test_app
):
    test_app.state.app_config.general_settings.upstream_http_read_timeout_seconds = 123
    test_app.state.app_config.general_settings.upstream_http_pool_timeout_seconds = 4
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params.pop("timeout", None)
    captured: dict[str, object] = {}

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del headers
        captured["timeout"] = timeout
        assert url.endswith("/chat/completions")
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1700000000,
                "model": json["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    timeout = captured["timeout"]
    assert getattr(timeout, "read") == 123.0
    assert getattr(timeout, "pool") == 4.0


@pytest.mark.asyncio
async def test_chat_completion_uses_startup_upstream_http_settings_snapshot(client, test_app):
    test_app.state.app_config.general_settings.upstream_http_read_timeout_seconds = 123
    test_app.state.upstream_http_settings = type(
        "StartupUpstreamHTTPSettings",
        (),
        {
            "upstream_http_read_timeout_seconds": 222,
            "upstream_http_pool_timeout_seconds": 3,
        },
    )()
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params.pop("timeout", None)
    captured: dict[str, object] = {}

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del url, headers
        captured["timeout"] = timeout
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1700000000,
                "model": json["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    timeout = captured["timeout"]
    assert getattr(timeout, "read") == 222.0
    assert getattr(timeout, "pool") == 3.0


@pytest.mark.asyncio
async def test_chat_completion_success_ignores_router_usage_write_failure(client, test_app):
    async def fail_usage(*args, **kwargs):  # noqa: ANN001, ANN201
        del args, kwargs
        raise ServiceUnavailableError(message="router usage unavailable")

    test_app.state.router_state_backend.increment_usage_counters = fail_usage

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert response.json()["object"] == "chat.completion"


def _mcp_success_post(upstream_calls: list[dict[str, object]]):  # noqa: ANN202
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

    return post


def _mcp_tool_call_response(model: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-tool-call",
            "object": "chat.completion",
            "created": 1700000000,
            "model": model,
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
        },
    )


def _mcp_final_response(model: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-tool-final",
            "object": "chat.completion",
            "created": 1700000001,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "done"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        },
    )


def _configure_chat_fallback(test_app):  # noqa: ANN001, ANN201
    registry_store = test_app.state.router.deployment_registry
    registry = list(registry_store["gpt-4o-mini"])
    primary = registry[0]
    fallback = type(primary)(
        deployment_id="gpt-4o-mini-mcp-fallback",
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
    return primary, fallback


@pytest.mark.asyncio
async def test_chat_completion_with_mcp_tool_auto_executes_and_audits(client, test_app):
    gateway = _FakeMCPGateway()
    audit = _RecordingAuditService()
    test_app.state.mcp_gateway_service = gateway
    test_app.state.audit_service = audit

    upstream_calls: list[dict[str, object]] = []
    test_app.state.http_client.post = _mcp_success_post(upstream_calls)
    headers = {
        "Authorization": f"Bearer {test_app.state._test_key}",
        "x-request-id": "req-mcp-chat",
    }
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Search docs for DeltaLLM"}],
        "tools": [
            {
                "type": "mcp",
                "server": "docs",
                "allowed_tools": ["search"],
                "require_approval": "never",
            }
        ],
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "done"
    assert len(upstream_calls) == 2
    assert gateway.tool_calls == [("docs.search", {"query": "delta"}, "req-mcp-chat")]
    assert any(
        getattr(event, "action", None) == AuditAction.MCP_TOOL_CALL.value
        for event, _, _ in audit.records
    )
    mcp_audits = [
        (event, critical)
        for event, _, critical in audit.records
        if getattr(event, "action", None) == AuditAction.MCP_TOOL_CALL.value
    ]
    assert [getattr(event, "status", None) for event, _ in mcp_audits] == [
        "attempted",
        "success",
    ]
    assert [critical for _, critical in mcp_audits] == [True, False]
    assert mcp_audits[0][0].event_id != mcp_audits[1][0].event_id


@pytest.mark.parametrize("followup_failure", ["429", "5xx", "timeout"])
@pytest.mark.asyncio
async def test_mcp_tool_is_not_replayed_when_followup_model_fails_over(
    client,
    test_app,
    followup_failure: str,
):
    gateway = _FakeMCPGateway()
    audit = _RecordingAuditService()
    test_app.state.mcp_gateway_service = gateway
    test_app.state.audit_service = audit
    primary, fallback = _configure_chat_fallback(test_app)
    upstream_calls: list[tuple[str | None, bool]] = []

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del timeout
        has_tool_result = any(message.get("role") == "tool" for message in json["messages"])
        authorization = headers.get("Authorization")
        upstream_calls.append((authorization, has_tool_result))
        if not has_tool_result:
            return _mcp_tool_call_response(json["model"])
        if authorization == "Bearer provider-key":
            request = httpx.Request("POST", url)
            if followup_failure == "timeout":
                raise httpx.ReadTimeout("final model timed out", request=request)
            status_code = 429 if followup_failure == "429" else 503
            return httpx.Response(
                status_code,
                json={"error": {"message": f"follow-up {followup_failure}"}},
                request=request,
            )
        return _mcp_final_response(json["model"])

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {test_app.state._test_key}",
            "x-request-id": f"req-mcp-followup-{followup_failure}",
        },
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Search docs"}],
            "tools": [
                {
                    "type": "mcp",
                    "server": "docs",
                    "allowed_tools": ["search"],
                    "require_approval": "never",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "done"
    assert response.headers["x-deltallm-route-deployment"] == fallback.deployment_id
    assert response.headers["x-deltallm-route-fallback-used"] == "true"
    assert gateway.tool_calls == [
        ("docs.search", {"query": "delta"}, f"req-mcp-followup-{followup_failure}")
    ]
    assert upstream_calls == [
        ("Bearer provider-key", False),
        ("Bearer provider-key", True),
        ("Bearer provider-key-fallback", True),
    ]
    tool_audits = [
        event
        for event, _, _ in audit.records
        if getattr(event, "action", None) == AuditAction.MCP_TOOL_CALL.value
    ]
    assert [event.status for event in tool_audits] == ["attempted", "success"]
    assert primary.deployment_id != fallback.deployment_id


@pytest.mark.asyncio
async def test_mcp_model_phases_keep_fallback_affinity_and_report_any_fallback(
    client,
    test_app,
):
    gateway = _FakeMCPGateway()
    test_app.state.mcp_gateway_service = gateway
    test_app.state.audit_service = _RecordingAuditService()
    primary, fallback = _configure_chat_fallback(test_app)
    upstream_calls: list[tuple[str | None, bool]] = []

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del timeout
        has_tool_result = any(message.get("role") == "tool" for message in json["messages"])
        authorization = headers.get("Authorization")
        upstream_calls.append((authorization, has_tool_result))
        if not has_tool_result and authorization == "Bearer provider-key":
            return httpx.Response(
                503,
                json={"error": {"message": "initial primary unavailable"}},
                request=httpx.Request("POST", url),
            )
        if not has_tool_result:
            return _mcp_tool_call_response(json["model"])
        if authorization == "Bearer provider-key-fallback":
            return httpx.Response(
                503,
                json={"error": {"message": "fallback follow-up unavailable"}},
                request=httpx.Request("POST", url),
            )
        return _mcp_final_response(json["model"])

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Search docs"}],
            "tools": [
                {
                    "type": "mcp",
                    "server": "docs",
                    "allowed_tools": ["search"],
                    "require_approval": "never",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "done"
    assert upstream_calls == [
        ("Bearer provider-key", False),
        ("Bearer provider-key-fallback", False),
        ("Bearer provider-key-fallback", True),
        ("Bearer provider-key", True),
    ]
    assert gateway.tool_calls == [("docs.search", {"query": "delta"}, None)]
    assert response.headers["x-deltallm-route-deployment"] == primary.deployment_id
    assert response.headers["x-deltallm-route-fallback-used"] == "true"
    assert primary.deployment_id != fallback.deployment_id


@pytest.mark.asyncio
async def test_mcp_tool_is_not_replayed_when_followup_model_retries(client, test_app):
    gateway = _FakeMCPGateway()
    test_app.state.mcp_gateway_service = gateway
    test_app.state.audit_service = _RecordingAuditService()
    test_app.state.failover_manager.config = replace(
        test_app.state.failover_manager.config,
        num_retries=1,
        retry_after=0.001,
        backoff_jitter=False,
    )
    upstream_phases: list[bool] = []

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del headers, timeout
        has_tool_result = any(message.get("role") == "tool" for message in json["messages"])
        upstream_phases.append(has_tool_result)
        if not has_tool_result:
            return _mcp_tool_call_response(json["model"])
        if upstream_phases.count(True) == 1:
            return httpx.Response(
                503,
                json={"error": {"message": "retry follow-up"}},
                request=httpx.Request("POST", url),
            )
        return _mcp_final_response(json["model"])

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Search docs"}],
            "tools": [
                {
                    "type": "mcp",
                    "server": "docs",
                    "allowed_tools": ["search"],
                    "require_approval": "never",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert upstream_phases == [False, True, True]
    assert gateway.tool_calls == [("docs.search", {"query": "delta"}, None)]
    assert response.headers["x-deltallm-route-fallback-used"] == "false"


@pytest.mark.asyncio
async def test_mcp_tool_success_survives_best_effort_audit_database_failure(client, test_app):
    gateway = _FakeMCPGateway()
    test_app.state.mcp_gateway_service = gateway
    test_app.state.audit_service = _FailingBestEffortAuditService()

    upstream_calls: list[dict[str, object]] = []
    test_app.state.http_client.post = _mcp_success_post(upstream_calls)
    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {test_app.state._test_key}",
            "x-request-id": "req-mcp-audit-outage",
        },
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Search docs for DeltaLLM"}],
            "tools": [
                {
                    "type": "mcp",
                    "server": "docs",
                    "allowed_tools": ["search"],
                    "require_approval": "never",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "done"
    assert len(upstream_calls) == 2
    assert gateway.tool_calls == [("docs.search", {"query": "delta"}, "req-mcp-audit-outage")]


@pytest.mark.asyncio
async def test_mcp_tool_required_attempt_audit_fails_before_execution(client, test_app):
    gateway = _FakeMCPGateway()
    test_app.state.mcp_gateway_service = gateway
    test_app.state.audit_service = _FailingRequiredAuditService()

    upstream_calls: list[dict[str, object]] = []
    test_app.state.http_client.post = _mcp_success_post(upstream_calls)
    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {test_app.state._test_key}",
            "x-request-id": "req-mcp-required-audit-outage",
        },
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Search docs for DeltaLLM"}],
            "tools": [
                {
                    "type": "mcp",
                    "server": "docs",
                    "allowed_tools": ["search"],
                    "require_approval": "never",
                }
            ],
        },
    )

    assert response.status_code == 503
    assert len(upstream_calls) == 1
    assert gateway.tool_calls == []


@pytest.mark.asyncio
async def test_mcp_tool_missing_audit_service_fails_before_execution(client, test_app):
    gateway = _FakeMCPGateway()
    test_app.state.mcp_gateway_service = gateway
    test_app.state.audit_service = None

    upstream_calls: list[dict[str, object]] = []
    test_app.state.http_client.post = _mcp_success_post(upstream_calls)
    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {test_app.state._test_key}",
            "x-request-id": "req-mcp-missing-audit",
        },
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Search docs for DeltaLLM"}],
            "tools": [
                {
                    "type": "mcp",
                    "server": "docs",
                    "allowed_tools": ["search"],
                    "require_approval": "never",
                }
            ],
        },
    )

    assert response.status_code == 503
    assert len(upstream_calls) == 1
    assert gateway.tool_calls == []


@pytest.mark.asyncio
async def test_chat_completion_with_mcp_tool_guardrail_blocks_tool_execution(client, test_app):
    gateway = _FakeMCPGateway()
    test_app.state.mcp_gateway_service = gateway
    test_app.state.guardrail_registry.register(
        BlockingMCPToolGuardrail(name="block-mcp-tool", default_on=True)
    )

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
                                "function": {
                                    "name": "docs.search",
                                    "arguments": '{"query":"delta"}',
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

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Search docs for DeltaLLM"}],
        "tools": [
            {
                "type": "mcp",
                "server": "docs",
                "allowed_tools": ["search"],
                "require_approval": "never",
            }
        ],
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
                                "function": {
                                    "name": "docs.search",
                                    "arguments": '{"query":"delta"}',
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

    test_app.state.http_client.post = post
    headers = {
        "Authorization": f"Bearer {test_app.state._test_key}",
        "x-request-id": "req-mcp-tool-fail",
    }
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Search docs for DeltaLLM"}],
        "tools": [
            {
                "type": "mcp",
                "server": "docs",
                "allowed_tools": ["search"],
                "require_approval": "never",
            }
        ],
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 503
    matching = [
        event
        for event, _, _ in audit.records
        if getattr(event, "action", None) == AuditAction.MCP_TOOL_CALL.value
    ]
    assert matching
    assert matching[-1].status == "error"
    assert matching[-1].request_id == "req-mcp-tool-fail"
    assert matching[-1].metadata["scope_type"] == "team"
    assert matching[-1].metadata["scope_id"] == "team-ops"


@pytest.mark.asyncio
async def test_chat_completion_with_mcp_tool_rate_limit_returns_429(client, test_app):
    test_app.state.mcp_gateway_service = _RateLimitedMCPGateway()
    test_app.state.audit_service = _RecordingAuditService()

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
                                "function": {
                                    "name": "docs.search",
                                    "arguments": '{"query":"delta"}',
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

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Search docs for DeltaLLM"}],
        "tools": [
            {
                "type": "mcp",
                "server": "docs",
                "allowed_tools": ["search"],
                "require_approval": "never",
            }
        ],
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 429
    assert response.json()["error"]["type"] == "rate_limit_error"


@pytest.mark.asyncio
async def test_chat_completion_with_mcp_tool_manual_approval_returns_400(client, test_app):
    test_app.state.mcp_gateway_service = _ManualApprovalMCPGateway()

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
                                "function": {
                                    "name": "docs.search",
                                    "arguments": '{"query":"delta"}',
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

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Search docs for DeltaLLM"}],
        "tools": [
            {
                "type": "mcp",
                "server": "docs",
                "allowed_tools": ["search"],
                "require_approval": "never",
            }
        ],
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert "manual approval" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_chat_completion_with_mcp_tool_timeout_returns_503(client, test_app):
    test_app.state.mcp_gateway_service = _TimeoutMCPGateway()
    test_app.state.audit_service = _RecordingAuditService()

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
                                "function": {
                                    "name": "docs.search",
                                    "arguments": '{"query":"delta"}',
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

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Search docs for DeltaLLM"}],
        "tools": [
            {
                "type": "mcp",
                "server": "docs",
                "allowed_tools": ["search"],
                "require_approval": "never",
            }
        ],
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "service_unavailable"


@pytest.mark.asyncio
async def test_chat_completion_with_mcp_orchestration_unexpected_error_returns_503(
    client, test_app, monkeypatch
):
    monkeypatch.setattr(chat_router, "MCPChatOrchestrator", _RaisingMCPOrchestrator)
    test_app.state.mcp_gateway_service = _FakeMCPGateway()

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Search docs for DeltaLLM"}],
            "tools": [
                {
                    "type": "mcp",
                    "server": "docs",
                    "allowed_tools": ["search"],
                    "require_approval": "never",
                }
            ],
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "service_unavailable"
    assert response.json()["error"]["message"] == "MCP orchestration failed"


@pytest.mark.asyncio
async def test_chat_completion_with_local_mcp_gateway_error_does_not_affect_deployment_health(
    client, test_app
):
    test_app.state.mcp_gateway_service = _BuggyMCPGateway()
    test_app.state.audit_service = _RecordingAuditService()
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del url, headers, json, timeout
        raise AssertionError("upstream provider should not be called")

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Search docs for DeltaLLM"}],
        "tools": [
            {
                "type": "mcp",
                "server": "docs",
                "allowed_tools": ["search"],
                "require_approval": "never",
            }
        ],
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "service_unavailable"
    assert response.json()["error"]["message"] == "MCP orchestration failed"
    health = await test_app.state.router_state_backend.get_health(deployment.deployment_id)
    assert health.get("healthy", "true") != "false"
    assert int(health.get("consecutive_failures", 0) or 0) == 0
    assert health.get("last_error") is None
    assert not await test_app.state.router_state_backend.is_cooled_down(deployment.deployment_id)


@pytest.mark.asyncio
async def test_chat_completion_without_mcp_tools_skips_mcp_gateway(client, test_app):
    test_app.state.mcp_gateway_service = _ExplodingMCPGateway()
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }

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
    deployment_id = str(response.headers["x-deltallm-route-deployment"])
    usage = await test_app.state.router_state_backend.get_usage(deployment_id)
    assert usage == {"rpm": 1, "tpm": 3}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "include_assistant_content",
    [pytest.param(False, id="omitted"), pytest.param(True, id="null")],
)
async def test_chat_completion_streaming_accepts_contentless_assistant_tool_call_history(
    client,
    test_app,
    include_assistant_content: bool,
) -> None:
    assistant: dict[str, object] = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "search", "arguments": "{}"},
            }
        ],
    }
    if include_assistant_content:
        assistant["content"] = None

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "search"},
                assistant,
                {"role": "tool", "tool_call_id": "call_1", "content": "result"},
            ],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "data: [DONE]" in response.text


@pytest.mark.asyncio
async def test_stream_releases_capacity_before_post_call_hooks(client, test_app):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    observed_active_requests: list[int] = []

    class CapacityObservingCallback(CustomLogger):
        async def async_post_call_success_hook(
            self,
            data: dict[str, Any],
            user_api_key_dict: dict[str, Any],
            response: Any,
        ) -> None:
            del data, user_api_key_dict, response
            observed_active_requests.append(
                await test_app.state.router_state_backend.get_active_requests(
                    deployment.deployment_id
                )
            )

    manager = CallbackManager()
    manager.register_callback(CapacityObservingCallback())
    test_app.state.callback_manager = manager

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert observed_active_requests == [0]


@pytest.mark.asyncio
async def test_chat_completion_streaming_success_ignores_router_usage_write_failure(
    client, test_app
):
    async def fail_usage(*args, **kwargs):  # noqa: ANN001, ANN201
        del args, kwargs
        raise ServiceUnavailableError(message="router usage unavailable")

    test_app.state.router_state_backend.increment_usage_counters = fail_usage

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert "data: [DONE]" in response.text


@pytest.mark.asyncio
async def test_chat_stream_health_update_failure_does_not_skip_billing_or_cleanup(client, test_app):
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info = {"input_cost_per_token": 1.0, "output_cost_per_token": 2.0}

    def stream(method, url, headers, json, timeout):  # noqa: ANN001, ANN201
        del method, url, headers, json, timeout
        return _StreamContext(
            status_code=200,
            lines=[
                'data: {"id":"chatcmpl-health","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":null}]}',
                'data: {"id":"chatcmpl-health","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}',
                "data: [DONE]",
            ],
        )

    async def fail_health_update(*args, **kwargs):  # noqa: ANN001, ANN201
        del args, kwargs
        raise ServiceUnavailableError(message="router health unavailable")

    test_app.state.http_client.stream = stream
    test_app.state.cooldown_manager.record_success = fail_health_update
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    await asyncio.sleep(0.05)
    assert recorder.events[-1]["usage"] == {
        "prompt_tokens": 2,
        "completion_tokens": 1,
        "total_tokens": 3,
    }
    assert (
        await test_app.state.router_state_backend.get_active_requests(deployment.deployment_id) == 0
    )
    metrics = await client.get("/metrics")
    assert "deltallm_router_health_update_failures_total" in metrics.text


@pytest.mark.asyncio
async def test_chat_completion_guardrail_blocks(client, test_app):
    test_app.state.guardrail_registry.register(
        BlockingChatGuardrail(name="block-chat", default_on=True)
    )
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
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    await asyncio.sleep(0.05)
    assert recorder.success == 1


@pytest.mark.asyncio
async def test_chat_completion_uses_explicit_provider_for_callbacks_and_spend(client, test_app):
    recorder = RecordingCallback()
    manager = CallbackManager()
    manager.register_callback(recorder, callback_type="success")
    test_app.state.callback_manager = manager
    test_app.state.spend_tracking_service = _SpendRecorder()
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params.update(
        {
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
            "api_base": "https://api.groq.com/openai/v1",
        }
    )

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    await asyncio.sleep(0.05)

    assert recorder.success == 1
    assert recorder.success_payloads[-1]["api_provider"] == "groq"
    assert recorder.success_payloads[-1]["deployment_model"] == "openai/gpt-oss-120b"
    spend_event = test_app.state.spend_tracking_service.events[-1]
    assert spend_event["metadata"]["provider"] == "groq"
    assert spend_event["metadata"]["deployment_model"] == "openai/gpt-oss-120b"


@pytest.mark.asyncio
async def test_reused_client_request_id_gets_distinct_server_billing_ids(client, test_app):
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    headers = {
        "Authorization": f"Bearer {test_app.state._test_key}",
        "x-request-id": "client-controlled-id",
    }
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }

    first = await client.post("/v1/chat/completions", headers=headers, json=body)
    second = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    await asyncio.wait_for(recorder.wait_for_events(2), timeout=0.5)

    assert len(recorder.events) == 2
    assert {event["request_id"] for event in recorder.events} == {"client-controlled-id"}
    event_ids = [event["event_id"] for event in recorder.events]
    assert event_ids[0] != event_ids[1]
    assert all(str(UUID(event_id)) == event_id for event_id in event_ids)


@pytest.mark.asyncio
async def test_chat_completion_runs_failure_callback(client, test_app):
    recorder = RecordingCallback()
    manager = CallbackManager()
    manager.register_callback(recorder, callback_type="failure")
    test_app.state.callback_manager = manager
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params.update(
        {
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
            "api_base": "https://api.groq.com/openai/v1",
        }
    )

    async def failing_post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        import httpx

        del headers, json, timeout
        return httpx.Response(
            503, json={"error": "unavailable"}, request=httpx.Request("POST", url)
        )

    test_app.state.http_client.post = failing_post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 503
    await asyncio.sleep(0.05)
    assert recorder.failure == 1
    assert recorder.failure_payloads[-1]["api_provider"] == "groq"
    assert recorder.failure_payloads[-1]["deployment_model"] == "openai/gpt-oss-120b"


@pytest.mark.asyncio
async def test_chat_upstream_rate_limit_returns_429(client, test_app):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del headers, json, timeout
        return httpx.Response(
            429,
            json={"error": {"message": "provider quota exhausted"}},
            headers={"Retry-After": "17"},
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "17"
    assert response.json()["error"] == {
        "message": "Provider rate limited request",
        "type": "rate_limit_error",
        "param": None,
        "code": None,
    }
    health = await test_app.state.router_state_backend.get_health(deployment.deployment_id)
    assert health.get("healthy", "true") != "false"
    assert int(health.get("consecutive_failures", 0) or 0) == 1
    assert health.get("last_error") == "Provider rate limited request"
    assert not await test_app.state.router_state_backend.is_cooled_down(deployment.deployment_id)


@pytest.mark.asyncio
async def test_chat_upstream_bad_request_does_not_mark_deployment_unhealthy(client, test_app):
    registry_store = test_app.state.router.deployment_registry
    registry = list(registry_store["gpt-4o-mini"])
    deployment = registry[0]
    deployment.deltallm_params["api_key"] = "provider-key"
    registry.append(
        type(deployment)(
            deployment_id="gpt-4o-mini-fallback",
            model_name="gpt-4o-mini",
            deltallm_params={"model": "openai/gpt-4o-mini", "api_key": "provider-key-fallback"},
            model_info={},
        )
    )
    registry_store.replace({**registry_store.snapshot(), "gpt-4o-mini": registry})

    async def choose_primary(model_group, request_context):  # noqa: ANN001, ANN201
        del model_group, request_context
        return deployment

    test_app.state.router.select_deployment = choose_primary
    calls = {"count": 0}
    attempted_auths: list[str | None] = []

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del timeout
        request = httpx.Request("POST", url)
        attempted_auths.append(headers.get("Authorization"))
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(400, json={"error": {"message": "bad input"}}, request=request)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-ok",
                "object": "chat.completion",
                "created": 1700000000,
                "model": json["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            request=request,
        )

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }

    failure = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert failure.status_code == 400
    assert attempted_auths == ["Bearer provider-key"]

    health = await test_app.state.router_state_backend.get_health(deployment.deployment_id)
    assert health.get("healthy", "true") != "false"
    assert int(health.get("consecutive_failures", 0) or 0) == 0
    assert health.get("last_error") is None
    assert not await test_app.state.router_state_backend.is_cooled_down(deployment.deployment_id)

    success = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert success.status_code == 200
    assert attempted_auths == ["Bearer provider-key", "Bearer provider-key"]


@pytest.mark.asyncio
async def test_chat_malformed_success_uses_general_fallback_without_leaking_payload(
    client,
    test_app,
):
    registry_store = test_app.state.router.deployment_registry
    registry = list(registry_store["gpt-4o-mini"])
    primary = registry[0]
    primary.deltallm_params["api_key"] = "provider-key"
    fallback = type(primary)(
        deployment_id="gpt-4o-mini-malformed-fallback",
        model_name="gpt-4o-mini",
        deltallm_params={
            "model": "openai/gpt-4o-mini",
            "api_key": "provider-key-fallback",
        },
        model_info={},
    )
    registry.append(fallback)
    registry_store.replace({**registry_store.snapshot(), "gpt-4o-mini": registry})

    async def choose_primary(model_group, request_context):  # noqa: ANN001, ANN201
        del model_group, request_context
        return primary

    attempted_auths: list[str | None] = []

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del timeout
        attempted_auths.append(headers.get("Authorization"))
        request = httpx.Request("POST", url)
        if headers.get("Authorization") == "Bearer provider-key":
            return httpx.Response(
                200,
                json={"secret": "sk-upstream", "messages": ["private-output"]},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-fallback",
                "object": "chat.completion",
                "created": 1700000000,
                "model": json["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            request=request,
        )

    test_app.state.router.select_deployment = choose_primary
    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "ok"
    assert response.headers["x-deltallm-route-deployment"] == fallback.deployment_id
    assert response.headers["x-deltallm-route-fallback-used"] == "true"
    assert attempted_auths == ["Bearer provider-key", "Bearer provider-key-fallback"]
    assert "sk-upstream" not in response.text
    assert "private-output" not in response.text
    health = await test_app.state.router_state_backend.get_health(primary.deployment_id)
    assert health["last_error"] == "Provider returned an invalid response"


class _StreamContext:
    def __init__(
        self,
        status_code: int,
        lines: list[str],
        *,
        body: bytes = b"",
        line_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self._lines = lines
        self._body = body
        self._line_error = line_error
        self.headers = httpx.Headers()
        self.exited = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line
        if self._line_error is not None:
            raise self._line_error

    async def aiter_bytes(self, chunk_size: int | None = None):
        del chunk_size
        if self._body:
            yield self._body


@pytest.mark.asyncio
async def test_chat_stream_billing_uses_provider_usage_when_present(client, test_app):
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info = {"input_cost_per_token": 1.0, "output_cost_per_token": 2.0}
    captured_payloads: list[dict[str, Any]] = []

    def stream(method: str, url: str, headers: dict[str, str], json: dict[str, Any], timeout: int):  # noqa: ANN001
        del method, url, headers, timeout
        captured_payloads.append(dict(json))
        return _StreamContext(
            status_code=200,
            lines=[
                'data: {"id":"chatcmpl-usage","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}',
                'data: {"id":"chatcmpl-usage","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":null}]}',
                'data: {"id":"chatcmpl-usage","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":10,"completion_tokens":4,"total_tokens":14}}',
                "data: [DONE]",
            ],
        )

    test_app.state.http_client.stream = stream
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200
    assert captured_payloads[-1]["stream_options"] == {"include_usage": True}
    assert '"usage"' not in response.text
    assert '"choices":[]' not in response.text
    deployment_id = str(response.headers["x-deltallm-route-deployment"])
    usage = await test_app.state.router_state_backend.get_usage(deployment_id)
    assert usage == {"rpm": 1, "tpm": 14}
    await asyncio.sleep(0.05)
    assert recorder.events[-1]["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
    }
    assert recorder.events[-1]["cost"] == 18.0
    assert recorder.events[-1]["metadata"]["usage_source"] == "provider"
    assert recorder.events[-1]["metadata"]["usage_estimated"] is False


@pytest.mark.asyncio
async def test_chat_stream_requests_usage_from_vllm(client, test_app):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params.update(
        {
            "provider": "vllm",
            "model": "zai-org/GLM-5.3-Flash",
            "api_base": "https://vllm.example/v1",
        }
    )
    captured_payloads: list[dict[str, Any]] = []

    def stream(method: str, url: str, headers: dict[str, str], json: dict[str, Any], timeout: int):  # noqa: ANN001
        del method, url, headers, timeout
        captured_payloads.append(dict(json))
        return _StreamContext(
            status_code=200,
            lines=[
                'data: {"id":"chatcmpl-vllm-usage","choices":[{"index":0,'
                '"delta":{"content":"ok"},"finish_reason":null}]}',
                'data: {"id":"chatcmpl-vllm-usage","choices":[],"usage":'
                '{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}}',
                "data: [DONE]",
            ],
        )

    test_app.state.http_client.stream = stream
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert captured_payloads[-1]["stream_options"] == {"include_usage": True}
    assert '"choices":[]' not in response.text


@pytest.mark.asyncio
async def test_chat_stream_default_usage_collection_does_not_expose_usage_chunk(client, test_app):
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info = {
        "input_cost_per_token": 1.0,
        "output_cost_per_token": 2.0,
        "default_params": {"stream_options": {"include_usage": True}},
    }
    captured_payloads: list[dict[str, Any]] = []

    def stream(method: str, url: str, headers: dict[str, str], json: dict[str, Any], timeout: int):  # noqa: ANN001
        del method, url, headers, timeout
        captured_payloads.append(dict(json))
        return _StreamContext(
            status_code=200,
            lines=[
                'data: {"id":"chatcmpl-default-usage","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":null}]}',
                'data: {"id":"chatcmpl-default-usage","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":10,"completion_tokens":4,"total_tokens":14}}',
                "data: [DONE]",
            ],
        )

    test_app.state.http_client.stream = stream
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200
    assert captured_payloads[-1]["stream_options"] == {"include_usage": True}
    assert '"usage"' not in response.text
    assert '"choices":[]' not in response.text
    await asyncio.sleep(0.05)
    assert recorder.events[-1]["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
    }
    assert recorder.events[-1]["cost"] == 18.0
    assert recorder.events[-1]["metadata"]["usage_source"] == "provider"


@pytest.mark.asyncio
async def test_chat_stream_forwards_usage_chunk_when_client_requested_usage(client, test_app):
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info = {"input_cost_per_token": 1.0, "output_cost_per_token": 2.0}
    captured_payloads: list[dict[str, Any]] = []

    def stream(method: str, url: str, headers: dict[str, str], json: dict[str, Any], timeout: int):  # noqa: ANN001
        del method, url, headers, timeout
        captured_payloads.append(dict(json))
        return _StreamContext(
            status_code=200,
            lines=[
                'data: {"id":"chatcmpl-usage","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":null}]}',
                'data: {"id":"chatcmpl-usage","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":10,"completion_tokens":4,"total_tokens":14}}',
                "data: [DONE]",
            ],
        )

    test_app.state.http_client.stream = stream
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200
    assert captured_payloads[-1]["stream_options"] == {"include_usage": True}
    assert '"usage"' in response.text
    assert '"choices":[]' in response.text
    await asyncio.sleep(0.05)
    assert recorder.events[-1]["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
    }
    assert recorder.events[-1]["metadata"]["usage_source"] == "provider"


@pytest.mark.asyncio
async def test_chat_stream_billing_estimates_reasoning_and_applies_tier_pricing(client, test_app):
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    test_app.state.tier_policy_service = _TierPricingService(
        {"input_cost_per_token": 4.0, "output_cost_per_token": 10.0}
    )
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info = {"input_cost_per_token": 1.0, "output_cost_per_token": 2.0}

    def stream(method: str, url: str, headers: dict[str, str], json: dict[str, Any], timeout: int):  # noqa: ANN001
        del method, url, headers, json, timeout
        return _StreamContext(
            status_code=200,
            lines=[
                'data: {"id":"chatcmpl-est","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}',
                'data: {"id":"chatcmpl-est","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"reasoning_content":"thinking"},"finish_reason":null}]}',
                "data: [DONE]",
            ],
        )

    test_app.state.http_client.stream = stream
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200
    deployment_id = str(response.headers["x-deltallm-route-deployment"])
    usage = await test_app.state.router_state_backend.get_usage(deployment_id)
    assert usage == {"rpm": 1, "tpm": 5}
    await asyncio.sleep(0.05)
    assert recorder.events[-1]["usage"] == {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "total_tokens": 5,
    }
    assert recorder.events[-1]["cost"] == 32.0
    assert recorder.events[-1]["metadata"]["provider_cost"] == 7.0
    assert recorder.events[-1]["metadata"]["pricing_source"] == "tier"
    assert recorder.events[-1]["metadata"]["customer_tier_key"] == "enterprise"
    assert recorder.events[-1]["metadata"]["usage_source"] == "estimated"
    assert recorder.events[-1]["metadata"]["usage_estimated"] is True
    assert recorder.events[-1]["metadata"]["usage_estimate_incomplete"] is False


@pytest.mark.asyncio
async def test_chat_billing_uses_deployment_pricing_when_tier_snapshot_is_stale(client, test_app):
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    test_app.state.tier_policy_service = _TierPricingService(
        {"input_cost_per_token": 4.0, "output_cost_per_token": 10.0},
        snapshot_stale=True,
    )
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info = {"input_cost_per_token": 1.0, "output_cost_per_token": 2.0}

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]}

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200
    await asyncio.sleep(0.05)
    assert recorder.events[-1]["usage"] == {
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_tokens": 2,
    }
    assert recorder.events[-1]["cost"] == 3.0
    assert recorder.events[-1]["metadata"]["provider_cost"] == 3.0
    assert recorder.events[-1]["metadata"]["pricing_source"] == "deployment"
    assert recorder.events[-1]["metadata"]["tier_snapshot_stale"] is True
    assert recorder.events[-1]["metadata"]["tier_pricing_authoritative"] is False
    assert recorder.events[-1]["metadata"]["tier_pricing_applied"] is False
    assert (
        recorder.events[-1]["metadata"]["tier_unavailable_reason"]
        == "tier_policy_unavailable_fail_open"
    )


@pytest.mark.asyncio
async def test_chat_billing_marks_partial_token_pricing_unpriced(client, test_app):
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info = {"input_cost_per_token": 1.0}

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200
    await asyncio.sleep(0.05)
    event = recorder.events[-1]
    assert event["usage"] == {
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_tokens": 2,
    }
    assert event["cost"] == 0.0
    assert event["metadata"]["billing_status"] == "unpriced"
    assert event["metadata"]["billing"]["unpriced_reason"] == ("no_configured_pricing")
    assert event["metadata"]["missing_pricing_fields"] == ["output_cost_per_token"]


@pytest.mark.asyncio
async def test_stream_retries_before_first_token_with_failover(client, test_app):
    registry_store = test_app.state.router.deployment_registry
    registry = list(registry_store["gpt-4o-mini"])
    registry.append(
        type(registry[0])(
            deployment_id="gpt-4o-mini-fallback",
            model_name="gpt-4o-mini",
            deltallm_params={"model": "openai/gpt-4o-mini", "api_key": "provider-key-fallback"},
            model_info={},
        )
    )
    registry_store.replace({**registry_store.snapshot(), "gpt-4o-mini": registry})

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
                'data: {"id":"chatcmpl-fb","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":null}]}',
                "data: [DONE]",
            ],
        )

    test_app.state.http_client.stream = stream
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    assert calls["count"] == 2
    assert response.headers["x-deltallm-route-deployment"] == "gpt-4o-mini-fallback"
    assert response.headers["x-deltallm-route-fallback-used"] == "true"


@pytest.mark.asyncio
async def test_stream_reasoning_commits_without_cooling_or_trying_fallback(client, test_app):
    primary, fallback = _configure_chat_fallback(test_app)
    test_app.state.cooldown_manager.allowed_fails = 0
    attempted_auths: list[str | None] = []

    def stream(method: str, url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001
        del method, url, json, timeout
        attempted_auths.append(headers.get("Authorization"))
        return _StreamContext(
            status_code=200,
            lines=[
                'data: {"id":"chatcmpl-reasoning","choices":[{"index":0,'
                '"delta":{"role":"assistant"},"finish_reason":null}]}',
                *[
                    "data: "
                    + jsonlib.dumps(
                        {
                            "id": "chatcmpl-reasoning",
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"reasoning_content": f"step-{index}"},
                                    "finish_reason": None,
                                }
                            ],
                        },
                        separators=(",", ":"),
                    )
                    for index in range(33)
                ],
                'data: {"id":"chatcmpl-reasoning","choices":[{"index":0,'
                '"delta":{"content":"answer"},"finish_reason":null}]}',
                'data: {"id":"chatcmpl-reasoning","choices":[{"index":0,'
                '"delta":{},"finish_reason":"stop"}]}',
                "data: [DONE]",
            ],
        )

    test_app.state.http_client.stream = stream
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert '"reasoning_content":"step-32"' in response.text
    assert '"content":"answer"' in response.text
    assert "data: [DONE]" in response.text
    assert attempted_auths == ["Bearer provider-key"]
    assert response.headers["x-deltallm-route-deployment"] == primary.deployment_id
    assert response.headers["x-deltallm-route-fallback-used"] == "false"
    for deployment in (primary, fallback):
        health = await test_app.state.router_state_backend.get_health(deployment.deployment_id)
        assert int(health.get("consecutive_failures", 0) or 0) == 0
        assert health.get("last_error") is None


@pytest.mark.asyncio
async def test_stream_does_not_fail_over_after_reasoning_output(client, test_app):
    primary, fallback = _configure_chat_fallback(test_app)
    test_app.state.cooldown_manager.allowed_fails = 0
    attempted_auths: list[str | None] = []

    def stream(method: str, url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001
        del method, url, json, timeout
        attempted_auths.append(headers.get("Authorization"))
        return _StreamContext(
            status_code=200,
            lines=[
                'data: {"id":"chatcmpl-reasoning-partial","choices":[{"index":0,'
                '"delta":{"reasoning_content":"partial thought"},"finish_reason":null}]}'
            ],
            line_error=httpx.ReadError("stream broke after reasoning output"),
        )

    test_app.state.http_client.stream = stream
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert "partial thought" in response.text
    assert "data: [DONE]" not in response.text
    assert attempted_auths == ["Bearer provider-key"]
    primary_health = await test_app.state.router_state_backend.get_health(primary.deployment_id)
    fallback_health = await test_app.state.router_state_backend.get_health(fallback.deployment_id)
    assert primary_health.get("last_error") == "Provider unavailable"
    assert fallback_health.get("last_error") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("unknown_frame_count", "include_terminal"),
    [(33, False), (2, True)],
)
async def test_stream_precommit_unknown_output_fails_over_without_cooldown(
    client,
    test_app,
    unknown_frame_count: int,
    include_terminal: bool,
):
    primary, fallback = _configure_chat_fallback(test_app)
    test_app.state.cooldown_manager.allowed_fails = 0
    spend = _SpendRecorder()
    audit = _RecordingAuditService()
    test_app.state.spend_tracking_service = spend
    test_app.state.audit_service = audit
    attempted_auths: list[str | None] = []

    def stream(method: str, url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001
        del method, url, json, timeout
        authorization = headers.get("Authorization")
        attempted_auths.append(authorization)
        if authorization == "Bearer provider-key-fallback":
            return _StreamContext(
                status_code=200,
                lines=[
                    'data: {"id":"chatcmpl-fallback","object":"chat.completion.chunk",'
                    '"choices":[{"index":0,"delta":{"content":"fallback ok"},'
                    '"finish_reason":null}]}',
                    'data: {"id":"chatcmpl-fallback","object":"chat.completion.chunk",'
                    '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
                    "data: [DONE]",
                ],
            )
        lines = [
            "data: "
            + jsonlib.dumps(
                {
                    "id": "chatcmpl-future-output",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"future_reasoning_field": f"step-{index}"},
                            "finish_reason": None,
                        }
                    ],
                },
                separators=(",", ":"),
            )
            for index in range(unknown_frame_count)
        ]
        if include_terminal:
            lines.append("data: [DONE]")
        return _StreamContext(status_code=200, lines=lines)

    test_app.state.http_client.stream = stream
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert '"content":"fallback ok"' in response.text
    assert attempted_auths == ["Bearer provider-key", "Bearer provider-key-fallback"]
    assert response.headers["x-deltallm-route-deployment"] == fallback.deployment_id
    assert response.headers["x-deltallm-route-fallback-used"] == "true"
    for deployment in (primary, fallback):
        health = await test_app.state.router_state_backend.get_health(deployment.deployment_id)
        assert int(health.get("consecutive_failures", 0) or 0) == 0
        assert health.get("last_error") is None

    await asyncio.wait_for(spend.wait_for_events(1), timeout=0.5)
    assert spend.events[-1]["metadata"]["stream"] is True
    chat_audits = [
        event
        for event, _, _ in audit.records
        if getattr(event, "action", None) == "CHAT_COMPLETION_REQUEST"
    ]
    assert chat_audits[-1].metadata["stream"] is True


@pytest.mark.asyncio
async def test_stream_precommit_role_only_limit_cools_primary_and_fails_over(client, test_app):
    primary, fallback = _configure_chat_fallback(test_app)
    test_app.state.cooldown_manager.allowed_fails = 0
    attempted_auths: list[str | None] = []

    def stream(method: str, url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001
        del method, url, json, timeout
        authorization = headers.get("Authorization")
        attempted_auths.append(authorization)
        if authorization == "Bearer provider-key-fallback":
            return _StreamContext(
                status_code=200,
                lines=[
                    'data: {"id":"chatcmpl-fallback","choices":[{"index":0,'
                    '"delta":{"content":"ok"},"finish_reason":null}]}',
                    'data: {"id":"chatcmpl-fallback","choices":[{"index":0,'
                    '"delta":{},"finish_reason":"stop"}]}',
                    "data: [DONE]",
                ],
            )
        return _StreamContext(
            status_code=200,
            lines=[
                'data: {"id":"chatcmpl-role-only","choices":[{"index":0,'
                '"delta":{"role":"assistant"},"finish_reason":null}]}'
                for _ in range(33)
            ],
        )

    test_app.state.http_client.stream = stream
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert attempted_auths == ["Bearer provider-key", "Bearer provider-key-fallback"]
    assert response.headers["x-deltallm-route-deployment"] == fallback.deployment_id
    primary_health = await test_app.state.router_state_backend.get_health(primary.deployment_id)
    assert primary_health["last_error"] == "Provider returned an invalid response"


@pytest.mark.asyncio
async def test_stream_content_filter_before_output_uses_specialized_fallback(client, test_app):
    registry_store = test_app.state.router.deployment_registry
    primary = registry_store["gpt-4o-mini"][0]
    primary.deltallm_params["api_key"] = "primary-key"
    fallback = type(primary)(
        deployment_id="gpt-4o-mini-content-fallback",
        model_name="gpt-4o-mini-content",
        deltallm_params={
            "model": "openai/gpt-4o-mini",
            "api_key": "content-fallback-key",
        },
        model_info={},
    )
    registry_store.replace(
        {
            **registry_store.snapshot(),
            "gpt-4o-mini": [primary],
            "gpt-4o-mini-content": [fallback],
        }
    )
    test_app.state.failover_manager.config = replace(
        test_app.state.failover_manager.config,
        content_policy_fallbacks={"gpt-4o-mini": ["gpt-4o-mini-content"]},
    )

    async def choose_primary(model_group, request_context):  # noqa: ANN001, ANN201
        del model_group, request_context
        return primary

    attempted_auths: list[str | None] = []

    def stream(method: str, url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001
        del method, url, json, timeout
        attempted_auths.append(headers.get("Authorization"))
        if headers.get("Authorization") == "Bearer primary-key":
            return _StreamContext(
                status_code=200,
                lines=[
                    'data: {"id":"chatcmpl-primary","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}',
                    'data: {"id":"chatcmpl-primary","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"content_filter"}]}',
                    "data: [DONE]",
                ],
            )
        return _StreamContext(
            status_code=200,
            lines=[
                'data: {"id":"chatcmpl-content-fallback","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}',
                'data: {"id":"chatcmpl-content-fallback","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"safe answer"},"finish_reason":null}]}',
                "data: [DONE]",
            ],
        )

    test_app.state.router.select_deployment = choose_primary
    test_app.state.http_client.stream = stream
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert "safe answer" in response.text
    assert "chatcmpl-primary" not in response.text
    assert attempted_auths == ["Bearer primary-key", "Bearer content-fallback-key"]
    assert response.headers["x-deltallm-route-deployment"] == fallback.deployment_id
    assert response.headers["x-deltallm-route-fallback-used"] == "true"
    primary_health = await test_app.state.router_state_backend.get_health(primary.deployment_id)
    assert int(primary_health.get("consecutive_failures", 0) or 0) == 0
    assert primary_health.get("last_error") is None
    primary_usage = await test_app.state.router_state_backend.get_usage(primary.deployment_id)
    fallback_usage = await test_app.state.router_state_backend.get_usage(fallback.deployment_id)
    assert int(primary_usage.get("rpm", 0) or 0) == 0
    assert fallback_usage["rpm"] == 1


@pytest.mark.asyncio
async def test_stream_terminal_only_success_uses_general_fallback(client, test_app):
    registry_store = test_app.state.router.deployment_registry
    primary = registry_store["gpt-4o-mini"][0]
    primary.deltallm_params["api_key"] = "primary-key"
    fallback = type(primary)(
        deployment_id="gpt-4o-mini-terminal-fallback",
        model_name="gpt-4o-mini",
        deltallm_params={
            "model": "openai/gpt-4o-mini",
            "api_key": "general-fallback-key",
        },
        model_info={},
    )
    registry_store.replace({**registry_store.snapshot(), "gpt-4o-mini": [primary, fallback]})

    async def choose_primary(model_group, request_context):  # noqa: ANN001, ANN201
        del model_group, request_context
        return primary

    attempted_auths: list[str | None] = []

    def stream(method: str, url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001
        del method, url, json, timeout
        attempted_auths.append(headers.get("Authorization"))
        if headers.get("Authorization") == "Bearer primary-key":
            return _StreamContext(
                status_code=200,
                lines=[
                    'data: {"id":"chatcmpl-terminal-only","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}',
                    "data: [DONE]",
                ],
            )
        return _StreamContext(
            status_code=200,
            lines=[
                'data: {"id":"chatcmpl-general-fallback","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"fallback answer"},"finish_reason":null}]}',
                "data: [DONE]",
            ],
        )

    test_app.state.router.select_deployment = choose_primary
    test_app.state.http_client.stream = stream
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert "fallback answer" in response.text
    assert "chatcmpl-terminal-only" not in response.text
    assert attempted_auths == ["Bearer primary-key", "Bearer general-fallback-key"]
    assert response.headers["x-deltallm-route-deployment"] == fallback.deployment_id
    assert response.headers["x-deltallm-route-fallback-used"] == "true"
    primary_health = await test_app.state.router_state_backend.get_health(primary.deployment_id)
    assert primary_health["last_error"] == "Provider returned an invalid response"
    primary_usage = await test_app.state.router_state_backend.get_usage(primary.deployment_id)
    fallback_usage = await test_app.state.router_state_backend.get_usage(fallback.deployment_id)
    assert int(primary_usage.get("rpm", 0) or 0) == 0
    assert fallback_usage["rpm"] == 1


@pytest.mark.asyncio
async def test_streaming_provider_4xx_body_is_classified_before_response_commit(client, test_app):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "azure_openai"
    deployment.deltallm_params["api_base"] = "https://azure.example/openai/v1"
    deployment.deltallm_params["api_key"] = "azure-provider-key"
    context = _StreamContext(
        status_code=400,
        lines=[],
        body=jsonlib.dumps(
            {
                "error": {
                    "code": "context_length_exceeded",
                    "message": "maximum context length sk-upstream",
                }
            }
        ).encode(),
    )

    def stream(method: str, url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001
        del method, url, json, timeout
        assert headers.get("api-key") == "azure-provider-key"
        return context

    test_app.state.http_client.stream = stream
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Provider rejected request"
    assert "sk-upstream" not in response.text
    assert context.exited is True


@pytest.mark.asyncio
async def test_stream_cancellation_closes_upstream_and_releases_active_permit(client, test_app):
    stream_started = asyncio.Event()
    keep_stream_open = asyncio.Event()

    class BlockingStreamContext(_StreamContext):
        async def aiter_lines(self):
            yield (
                'data: {"id":"chatcmpl-cancel","object":"chat.completion.chunk",'
                '"choices":[{"index":0,"delta":{"role":"assistant"},'
                '"finish_reason":null}]}'
            )
            stream_started.set()
            await keep_stream_open.wait()

    context = BlockingStreamContext(status_code=200, lines=[])

    def stream(method: str, url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001
        del method, url, headers, json, timeout
        return context

    test_app.state.http_client.stream = stream
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    request_task = asyncio.create_task(
        client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {test_app.state._test_key}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            },
        )
    )
    await asyncio.wait_for(stream_started.wait(), timeout=1.0)

    assert (
        await test_app.state.router_state_backend.get_active_requests(deployment.deployment_id) == 1
    )

    request_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request_task

    assert context.exited is True
    assert (
        await test_app.state.router_state_backend.get_active_requests(deployment.deployment_id) == 0
    )
    health = await test_app.state.router_state_backend.get_health(deployment.deployment_id)
    assert int(health.get("consecutive_failures", 0) or 0) == 0
    assert health.get("last_error") is None


@pytest.mark.asyncio
async def test_stream_failure_after_failover_uses_last_attempted_deployment(client, test_app):
    test_app.state.spend_tracking_service = _SpendRecorder()

    registry_store = test_app.state.router.deployment_registry
    registry = list(registry_store["gpt-4o-mini"])
    registry[0].deltallm_params["api_base"] = "https://primary.example/v1"
    registry[0].deltallm_params["api_key"] = "provider-key"
    fallback = type(registry[0])(
        deployment_id="gpt-4o-mini-fallback",
        model_name="gpt-4o-mini",
        deltallm_params={
            "model": "openai/gpt-4o-mini",
            "api_key": "provider-key-fallback",
            "api_base": "https://fallback.example/v1",
        },
        model_info={},
    )
    registry.append(fallback)
    registry_store.replace({**registry_store.snapshot(), "gpt-4o-mini": registry})

    async def choose_primary(model_group, request_context):  # noqa: ANN001, ANN201
        del request_context
        return test_app.state.router.deployment_registry[model_group][0]

    test_app.state.router.select_deployment = choose_primary

    stream_contexts: list[_StreamContext] = []

    def stream(method: str, url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001
        del method, url, json, timeout
        auth = headers.get("Authorization", "")
        if auth.endswith("provider-key"):
            context = _StreamContext(status_code=503, lines=[])
        else:
            context = _StreamContext(
                status_code=200,
                lines=[
                    'data: {"id":"chatcmpl-fb","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":"hi"},"finish_reason":null}]}',
                ],
                line_error=httpx.ReadError("fallback stream broke"),
            )
        stream_contexts.append(context)
        return context

    test_app.state.http_client.stream = stream
    headers = {
        "Authorization": f"Bearer {test_app.state._test_key}",
        "x-request-id": "req-stream-fallback-failure",
    }
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200
    assert "hi" in response.text
    assert "data: [DONE]" not in response.text

    await asyncio.sleep(0.05)
    assert len(test_app.state.spend_tracking_service.events) == 1
    last = test_app.state.spend_tracking_service.events[-1]
    metadata = last.get("metadata") or {}
    assert last["status"] == "error"
    assert last["call_type"] == "completion"
    assert last["error_type"] == "service_unavailable"
    assert metadata.get("api_base") == "https://fallback.example/v1"
    assert metadata.get("deployment_model") == "openai/gpt-4o-mini"

    primary_health = await test_app.state.router_state_backend.get_health(registry[0].deployment_id)
    fallback_health = await test_app.state.router_state_backend.get_health("gpt-4o-mini-fallback")
    assert primary_health.get("last_error") == "Provider unavailable"
    assert fallback_health.get("last_error") == "Provider unavailable"
    assert len(stream_contexts) == 2
    assert all(context.exited for context in stream_contexts)
    assert (
        await test_app.state.router_state_backend.get_active_requests(fallback.deployment_id) == 0
    )


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
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_chat_completion_uses_custom_auth_headers_for_openai_compatible_provider(
    client, test_app
):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "vllm"
    deployment.deltallm_params["api_base"] = "https://vllm.example/v1"
    deployment.deltallm_params["api_key"] = "vllm-provider-key"
    deployment.deltallm_params["auth_header_name"] = "X-Provider-Auth"
    deployment.deltallm_params["auth_header_format"] = "Token {api_key}"

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del timeout
        assert url.endswith("/chat/completions")
        assert headers.get("X-Provider-Auth") == "Token vllm-provider-key"
        assert "Authorization" not in headers
        payload = {
            "id": "chatcmpl-vllm",
            "object": "chat.completion",
            "created": 1700000000,
            "model": json["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }
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
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
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
async def test_chat_completion_does_not_forward_stream_options_for_non_stream_request(
    client, test_app
):
    captured_payloads: list[dict[str, Any]] = []
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info = {"default_params": {"stream_options": {"include_usage": True}}}

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del url, headers, timeout
        captured_payloads.append(dict(json))
        payload = {
            "id": "chatcmpl-no-stream-options",
            "object": "chat.completion",
            "created": 1700000000,
            "model": json["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
        "stream_options": {"include_usage": True},
    }
    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200
    assert captured_payloads
    assert "stream_options" not in captured_payloads[-1]


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
            "usageMetadata": {
                "promptTokenCount": 1,
                "candidatesTokenCount": 1,
                "totalTokenCount": 2,
            },
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }
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
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }
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
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "ok"


@pytest.mark.asyncio
async def test_chat_completion_bedrock_omits_implicit_sampling_defaults(client, test_app):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "bedrock"
    deployment.deltallm_params["model"] = "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
    deployment.deltallm_params["region"] = "us-east-1"
    deployment.deltallm_params["aws_access_key_id"] = "AKIDEXAMPLE"
    deployment.deltallm_params["aws_secret_access_key"] = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
    captured: dict[str, object] = {}

    async def post(url, headers, json=None, content=None, timeout=0):  # noqa: ANN001, ANN201
        del url, headers, json, timeout
        assert content is not None
        captured["payload"] = jsonlib.loads(content.decode("utf-8"))
        payload = {
            "requestId": "req_123",
            "output": {"message": {"content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        },
    )

    assert response.status_code == 200
    assert "inferenceConfig" not in captured["payload"]


@pytest.mark.asyncio
async def test_chat_completion_bedrock_preserves_explicit_null_sampling_params(client, test_app):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "bedrock"
    deployment.deltallm_params["model"] = "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
    deployment.deltallm_params["region"] = "us-east-1"
    deployment.deltallm_params["aws_access_key_id"] = "AKIDEXAMPLE"
    deployment.deltallm_params["aws_secret_access_key"] = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
    captured: dict[str, object] = {}

    async def post(url, headers, json=None, content=None, timeout=0):  # noqa: ANN001, ANN201
        del url, headers, json, timeout
        assert content is not None
        captured["payload"] = jsonlib.loads(content.decode("utf-8"))
        payload = {
            "requestId": "req_123",
            "output": {"message": {"content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        }
        return httpx.Response(200, json=payload)

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
            "top_p": None,
        },
    )

    assert response.status_code == 200
    assert "inferenceConfig" not in captured["payload"]
