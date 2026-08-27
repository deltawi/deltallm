from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from src.chat.mcp_execution import MCPChatExecutionService, MCPModelRouting
from src.mcp.models import MCPToolCallResult
from src.mcp.orchestrator import MCPChatOrchestrator, MCPRequestContext
from src.models.errors import ServiceUnavailableError, TimeoutError
from src.models.requests import ChatCompletionRequest
from src.models.responses import UserAPIKeyAuth
from src.router import ROUTING_MODE_CONTEXT_KEY, RequestDeadline


class _RecordingAuditService:
    def __init__(self) -> None:
        self.records: list[object] = []

    def record_event(self, event, *, payloads=None, critical=False):  # noqa: ANN001, ANN201
        del payloads, critical
        self.records.append(event)


class _RecordingGateway:
    def __init__(self, *, after_call: Callable[[], None] | None = None) -> None:
        self.tool_calls: list[str] = []
        self.after_call = after_call

    async def list_visible_tools(self, auth):  # noqa: ANN001, ANN201
        del auth
        return [
            SimpleNamespace(
                server_key="docs",
                original_name="search",
                namespaced_name="docs.search",
                description="Search docs",
                input_schema={"type": "object"},
                scope_type="team",
                scope_id="team-default",
            )
        ]

    async def tool_requires_manual_approval(self, auth, *, server_key, tool_name):  # noqa: ANN001, ANN201
        del auth, server_key, tool_name
        return False

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
        del auth, arguments, request_headers, request_id, correlation_id
        self.tool_calls.append(namespaced_tool_name)
        if self.after_call is not None:
            self.after_call()
        return MCPToolCallResult(
            content=[{"type": "text", "text": "result"}],
            structured_content={"answer": "result"},
            is_error=False,
        )


class _RaisingOrchestrator:
    async def execute(
        self, request_context, auth, payload, execute_chat_call, guardrail_middleware
    ):  # noqa: ANN001, ANN002, ANN003, ANN204
        del request_context, auth, payload, execute_chat_call, guardrail_middleware
        raise RuntimeError("MCP orchestration failed unexpectedly")


def _tool_call_response() -> dict[str, Any]:
    return {
        "id": "chatcmpl-tool",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "gpt-4o-mini",
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
                                "arguments": json.dumps({"query": "delta"}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _request_payload() -> ChatCompletionRequest:
    return ChatCompletionRequest.model_validate(
        {
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
        }
    )


def _request_context(request_id: str) -> MCPRequestContext:
    return MCPRequestContext(
        request_headers={},
        request_id=request_id,
        correlation_id=request_id,
        client_ip=None,
        user_agent=None,
    )


def _routing(test_app) -> MCPModelRouting:  # noqa: ANN001
    primary = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    return MCPModelRouting(
        primary_deployment=primary,
        model_group="gpt-4o-mini",
        routing_context={
            "metadata": {},
            ROUTING_MODE_CONTEXT_KEY: "chat",
        },
    )


def _auth() -> UserAPIKeyAuth:
    return UserAPIKeyAuth(api_key="sk-test", models=["gpt-4o-mini"])


@pytest.mark.asyncio
async def test_cancellation_after_tool_success_does_not_replay_tool(test_app) -> None:
    gateway = _RecordingGateway()
    audit = _RecordingAuditService()
    manager = test_app.state.failover_manager
    routing = _routing(test_app)
    final_model_started = asyncio.Event()
    never_complete = asyncio.Event()
    observed_deadlines: list[RequestDeadline] = []
    original_execute_with_failover = manager.execute_with_failover

    async def recording_execute_with_failover(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        observed_deadlines.append(kwargs["request_deadline"])
        return await original_execute_with_failover(*args, **kwargs)

    manager.execute_with_failover = recording_execute_with_failover

    async def execute_chat(payload, deployment):  # noqa: ANN001, ANN202
        del deployment
        if any(message.role == "tool" for message in payload.messages):
            final_model_started.set()
            await never_complete.wait()
        return _tool_call_response(), 1.0

    service = MCPChatExecutionService(
        failover_manager=manager,
        orchestrator=MCPChatOrchestrator(gateway, audit_service=audit),  # type: ignore[arg-type]
        execute_chat_call=execute_chat,
    )
    async with asyncio.TaskGroup() as task_group:
        task = task_group.create_task(
            service.execute(
                request_context=_request_context("req-cancel"),
                auth=_auth(),
                payload=_request_payload(),
                guardrail_middleware=test_app.state.guardrail_middleware,
                routing=routing,
            )
        )
        await asyncio.wait_for(final_model_started.wait(), timeout=1)
        task.cancel()

    assert task.cancelled()
    assert gateway.tool_calls == ["docs.search"]
    assert len(observed_deadlines) == 2
    assert observed_deadlines[0] is observed_deadlines[1]
    assert (
        await test_app.state.router_state_backend.get_active_requests(
            routing.primary_deployment.deployment_id
        )
        == 0
    )
    assert [getattr(event, "status", None) for event in audit.records] == [
        "attempted",
        "success",
    ]


@pytest.mark.asyncio
async def test_tool_and_model_phases_share_one_total_deadline(test_app) -> None:
    manager = test_app.state.failover_manager
    deadline = RequestDeadline.after(60)
    gateway = _RecordingGateway(
        after_call=lambda: object.__setattr__(
            deadline,
            "expires_at",
            asyncio.get_running_loop().time(),
        )
    )
    audit = _RecordingAuditService()
    model_calls = 0
    manager.create_request_deadline = lambda timeout_seconds=None: deadline

    async def execute_chat(payload, deployment):  # noqa: ANN001, ANN202
        nonlocal model_calls
        del payload, deployment
        model_calls += 1
        return _tool_call_response(), 1.0

    service = MCPChatExecutionService(
        failover_manager=manager,
        orchestrator=MCPChatOrchestrator(gateway, audit_service=audit),  # type: ignore[arg-type]
        execute_chat_call=execute_chat,
    )

    with pytest.raises(TimeoutError, match="Request deadline exceeded"):
        await service.execute(
            request_context=_request_context("req-deadline"),
            auth=_auth(),
            payload=_request_payload(),
            guardrail_middleware=test_app.state.guardrail_middleware,
            routing=_routing(test_app),
        )

    assert model_calls == 1
    assert gateway.tool_calls == ["docs.search"]
    assert [getattr(event, "status", None) for event in audit.records] == [
        "attempted",
        "success",
    ]


@pytest.mark.asyncio
async def test_execute_classifies_unexpected_orchestrator_exceptions(test_app) -> None:
    service = MCPChatExecutionService(
        failover_manager=test_app.state.failover_manager,
        orchestrator=_RaisingOrchestrator(),  # type: ignore[arg-type]
        execute_chat_call=lambda phase_payload, deployment: (_tool_call_response(), 1.0),  # noqa: ARG005
    )

    with pytest.raises(ServiceUnavailableError, match="MCP orchestration failed") as exc_info:
        await service.execute(
            request_context=_request_context("req-orchestrator-fail"),
            auth=_auth(),
            payload=_request_payload(),
            guardrail_middleware=test_app.state.guardrail_middleware,
            routing=_routing(test_app),
        )

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert "unexpectedly" not in str(exc_info.value)
