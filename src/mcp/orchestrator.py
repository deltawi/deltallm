from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from fastapi import Request

from src.audit.actions import AuditAction
from src.chat.audit import request_client_ip
from src.models.errors import ApprovalRequiredError, InvalidRequestError, PermissionDeniedError, RateLimitError, ServiceUnavailableError
from src.models.requests import ChatCompletionRequest, ChatMessage, FunctionToolDefinition, MCPToolDefinition
from src.services.audit_service import AuditEventInput, AuditPayloadInput, AuditService

from .exceptions import (
    MCPAccessDeniedError,
    MCPApprovalRequiredError,
    MCPError,
    MCPInvalidResponseError,
    MCPPolicyDeniedError,
    MCPRateLimitError,
    MCPToolTimeoutError,
    MCPToolNotFoundError,
    MCPTransportError,
)
from .gateway import MCPGatewayService


@dataclass(frozen=True)
class ResolvedMCPTool:
    server_key: str
    original_name: str
    namespaced_name: str
    description: str | None
    input_schema: dict[str, Any]
    scope_type: str | None = None
    scope_id: str | None = None


def chat_request_has_mcp_tools(payload: ChatCompletionRequest) -> bool:
    return any(isinstance(tool, MCPToolDefinition) for tool in payload.tools or [])


class MCPChatOrchestrator:
    def __init__(
        self,
        gateway: MCPGatewayService,
        *,
        max_mcp_tool_hops: int = 4,
        max_mcp_tools_per_turn: int = 8,
    ) -> None:
        self.gateway = gateway
        self.max_mcp_tool_hops = max(1, int(max_mcp_tool_hops))
        self.max_mcp_tools_per_turn = max(1, int(max_mcp_tools_per_turn))

    async def prepare_payload(
        self,
        auth: Any,
        payload: ChatCompletionRequest,
    ) -> tuple[ChatCompletionRequest, dict[str, ResolvedMCPTool]]:
        requested_mcp_tools = [tool for tool in payload.tools or [] if isinstance(tool, MCPToolDefinition)]
        if not requested_mcp_tools:
            return payload, {}

        visible = await self.gateway.list_visible_tools(auth)
        translated: list[FunctionToolDefinition] = [
            tool if isinstance(tool, FunctionToolDefinition) else FunctionToolDefinition.model_validate(tool.model_dump(mode="python"))
            for tool in (payload.tools or [])
            if isinstance(tool, FunctionToolDefinition)
        ]
        resolved_tools: dict[str, ResolvedMCPTool] = {}

        for definition in requested_mcp_tools:
            matched = [
                tool
                for tool in visible
                if tool.server_key == definition.server
                and (definition.allowed_tools is None or tool.original_name in set(definition.allowed_tools))
            ]
            if not matched:
                raise InvalidRequestError(message=f"No visible MCP tools are available for server '{definition.server}'")
            for tool in matched:
                if tool.namespaced_name in resolved_tools:
                    continue
                resolved_tools[tool.namespaced_name] = ResolvedMCPTool(
                    server_key=tool.server_key,
                    original_name=tool.original_name,
                    namespaced_name=tool.namespaced_name,
                    description=tool.description,
                    input_schema=dict(tool.input_schema or {}),
                    scope_type=getattr(tool, "scope_type", None),
                    scope_id=getattr(tool, "scope_id", None),
                )
                translated.append(
                    FunctionToolDefinition(
                        function={
                            "name": tool.namespaced_name,
                            "description": tool.description,
                            "parameters": dict(tool.input_schema or {"type": "object"}),
                        }
                    )
                )

        return payload.model_copy(update={"tools": translated}), resolved_tools

    async def execute(
        self,
        *,
        request: Request,
        auth: Any,
        payload: ChatCompletionRequest,
        execute_chat_call: Any,
        guardrail_middleware: Any,
    ) -> tuple[dict[str, Any], float]:
        translated_payload, resolved_tools = await self.prepare_payload(auth, payload)
        if not resolved_tools:
            return await execute_chat_call(translated_payload)

        current_payload = translated_payload
        total_api_latency_ms = 0.0

        for _ in range(self.max_mcp_tool_hops + 1):
            response_payload, api_latency_ms = await execute_chat_call(current_payload)
            total_api_latency_ms += api_latency_ms
            tool_calls = self._extract_tool_calls(response_payload)
            if not tool_calls:
                return response_payload, total_api_latency_ms

            if len(tool_calls) > self.max_mcp_tools_per_turn:
                raise InvalidRequestError(
                    message=f"MCP tool execution limit exceeded: received {len(tool_calls)} tool calls, limit is {self.max_mcp_tools_per_turn}"
                )

            if any(self._tool_name_from_call(tool_call) not in resolved_tools for tool_call in tool_calls):
                return response_payload, total_api_latency_ms

            next_messages = list(current_payload.messages)
            next_messages.append(self._assistant_message_from_response(response_payload))
            for tool_call in tool_calls:
                tool_name = self._tool_name_from_call(tool_call)
                resolved = resolved_tools[tool_name]
                arguments = self._arguments_from_tool_call(tool_call)
                guarded_arguments = await self._run_tool_input_guardrails(
                    guardrail_middleware=guardrail_middleware,
                    auth=auth,
                    server_key=resolved.server_key,
                    tool_name=resolved.original_name,
                    arguments=arguments,
                )
                tool_started = perf_counter()
                try:
                    result = await self.gateway.call_tool(
                        auth,
                        namespaced_tool_name=tool_name,
                        arguments=guarded_arguments,
                        request_headers=dict(request.headers),
                        request_id=request.headers.get("x-request-id"),
                        correlation_id=request.headers.get("x-request-id"),
                    )
                except MCPToolNotFoundError as exc:
                    self._emit_tool_audit_failure_event(
                        request=request,
                        auth=auth,
                        namespaced_tool_name=tool_name,
                        server_key=resolved.server_key,
                        scope_type=resolved.scope_type,
                        scope_id=resolved.scope_id,
                        arguments=guarded_arguments,
                        request_start=tool_started,
                        error=exc,
                    )
                    raise InvalidRequestError(message=str(exc)) from exc
                except MCPApprovalRequiredError as exc:
                    self._emit_tool_audit_failure_event(
                        request=request,
                        auth=auth,
                        namespaced_tool_name=tool_name,
                        server_key=resolved.server_key,
                        scope_type=resolved.scope_type,
                        scope_id=resolved.scope_id,
                        arguments=guarded_arguments,
                        request_start=tool_started,
                        error=exc,
                    )
                    raise ApprovalRequiredError(
                        message=str(exc),
                        approval_request_id=exc.approval_request_id,
                    ) from exc
                except (MCPAccessDeniedError, MCPPolicyDeniedError) as exc:
                    self._emit_tool_audit_failure_event(
                        request=request,
                        auth=auth,
                        namespaced_tool_name=tool_name,
                        server_key=resolved.server_key,
                        scope_type=resolved.scope_type,
                        scope_id=resolved.scope_id,
                        arguments=guarded_arguments,
                        request_start=tool_started,
                        error=exc,
                    )
                    raise PermissionDeniedError(message=str(exc)) from exc
                except MCPRateLimitError as exc:
                    self._emit_tool_audit_failure_event(
                        request=request,
                        auth=auth,
                        namespaced_tool_name=tool_name,
                        server_key=resolved.server_key,
                        scope_type=resolved.scope_type,
                        scope_id=resolved.scope_id,
                        arguments=guarded_arguments,
                        request_start=tool_started,
                        error=exc,
                    )
                    raise RateLimitError(message=str(exc), retry_after=exc.retry_after) from exc
                except (MCPTransportError, MCPInvalidResponseError, MCPToolTimeoutError, MCPError) as exc:
                    self._emit_tool_audit_failure_event(
                        request=request,
                        auth=auth,
                        namespaced_tool_name=tool_name,
                        server_key=resolved.server_key,
                        scope_type=resolved.scope_type,
                        scope_id=resolved.scope_id,
                        arguments=guarded_arguments,
                        request_start=tool_started,
                        error=exc,
                    )
                    raise ServiceUnavailableError(message=str(exc)) from exc

                guarded_result = await self._run_tool_output_guardrails(
                    guardrail_middleware=guardrail_middleware,
                    auth=auth,
                    server_key=resolved.server_key,
                    tool_name=resolved.original_name,
                    result=result,
                )
                self._emit_tool_audit_event(
                    request=request,
                    auth=auth,
                    namespaced_tool_name=tool_name,
                    server_key=resolved.server_key,
                    scope_type=resolved.scope_type,
                    scope_id=resolved.scope_id,
                    arguments=guarded_arguments,
                    result_payload=guarded_result,
                    request_start=tool_started,
                )
                next_messages.append(
                    ChatMessage(
                        role="tool",
                        content=self._tool_message_content(guarded_result),
                        tool_call_id=str(tool_call.get("id") or tool_name),
                    )
                )

            current_payload = current_payload.model_copy(update={"messages": next_messages})

        raise InvalidRequestError(message="MCP tool execution exceeded the configured hop limit")

    @staticmethod
    def _extract_tool_calls(response_payload: dict[str, Any]) -> list[dict[str, Any]]:
        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return []
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        message = first_choice.get("message")
        if not isinstance(message, dict):
            return []
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            return []
        return [item for item in tool_calls if isinstance(item, dict)]

    @staticmethod
    def _assistant_message_from_response(response_payload: dict[str, Any]) -> ChatMessage:
        choice = ((response_payload.get("choices") or [{}])[0]) if isinstance(response_payload.get("choices"), list) else {}
        message = choice.get("message") if isinstance(choice, dict) else {}
        if not isinstance(message, dict):
            message = {}
        return ChatMessage(
            role="assistant",
            content=message.get("content") if isinstance(message.get("content"), list) else str(message.get("content") or ""),
            name=message.get("name"),
            tool_calls=message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else None,
        )

    @staticmethod
    def _tool_name_from_call(tool_call: dict[str, Any]) -> str:
        function = tool_call.get("function")
        if not isinstance(function, dict):
            raise InvalidRequestError(message="Provider returned an invalid tool call payload")
        name = str(function.get("name") or "").strip()
        if not name:
            raise InvalidRequestError(message="Provider returned a tool call without a function name")
        return name

    @staticmethod
    def _arguments_from_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
        function = tool_call.get("function")
        if not isinstance(function, dict):
            raise InvalidRequestError(message="Provider returned an invalid tool call payload")
        raw_arguments = function.get("arguments")
        if raw_arguments is None:
            return {}
        if isinstance(raw_arguments, dict):
            return dict(raw_arguments)
        if isinstance(raw_arguments, str):
            try:
                parsed = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                raise InvalidRequestError(message="Provider returned invalid JSON tool arguments") from exc
            if not isinstance(parsed, dict):
                raise InvalidRequestError(message="Provider returned non-object tool arguments")
            return parsed
        raise InvalidRequestError(message="Provider returned unsupported tool arguments")

    async def _run_tool_input_guardrails(
        self,
        *,
        guardrail_middleware: Any,
        auth: Any,
        server_key: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        payload = await guardrail_middleware.run_pre_call(
            request_data={
                "server": server_key,
                "tool_name": tool_name,
                "arguments": arguments,
            },
            user_api_key_dict=auth.model_dump(mode="python"),
            call_type="mcp_tool",
        )
        guarded_arguments = payload.get("arguments") if isinstance(payload, dict) else None
        if guarded_arguments is None:
            return {}
        if not isinstance(guarded_arguments, dict):
            raise InvalidRequestError(message="Guardrail mutated MCP tool arguments into an invalid shape")
        return guarded_arguments

    async def _run_tool_output_guardrails(
        self,
        *,
        guardrail_middleware: Any,
        auth: Any,
        server_key: str,
        tool_name: str,
        result: Any,
    ) -> dict[str, Any]:
        payload = await guardrail_middleware.run_pre_call(
            request_data={
                "server": server_key,
                "tool_name": tool_name,
                "content": list(result.content),
                "structured_content": result.structured_content,
                "is_error": result.is_error,
                "metadata": dict(result.metadata or {}),
            },
            user_api_key_dict=auth.model_dump(mode="python"),
            call_type="mcp_tool_output",
        )
        if not isinstance(payload, dict):
            raise InvalidRequestError(message="Guardrail mutated MCP tool output into an invalid shape")
        return payload

    @staticmethod
    def _tool_message_content(result_payload: dict[str, Any]) -> str:
        structured = result_payload.get("structured_content")
        if isinstance(structured, dict):
            return json.dumps(structured, separators=(",", ":"), sort_keys=True)
        content = result_payload.get("content")
        if isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict):
            text = content[0].get("text")
            if isinstance(text, str):
                return text
        if isinstance(content, list):
            return json.dumps(content, separators=(",", ":"), sort_keys=True)
        return json.dumps(result_payload, separators=(",", ":"), sort_keys=True)

    def _emit_tool_audit_event(
        self,
        *,
        request: Request,
        auth: Any,
        namespaced_tool_name: str,
        server_key: str,
        scope_type: str | None,
        scope_id: str | None,
        arguments: dict[str, Any],
        result_payload: dict[str, Any],
        request_start: float,
    ) -> None:
        audit_service: AuditService | None = getattr(request.app.state, "audit_service", None)
        if audit_service is None:
            return
        request_id = request.headers.get("x-request-id")
        audit_service.record_event(
            AuditEventInput(
                action=AuditAction.MCP_TOOL_CALL.value,
                organization_id=getattr(auth, "organization_id", None),
                actor_type="api_key",
                actor_id=getattr(auth, "user_id", None) or getattr(auth, "api_key", None),
                api_key=getattr(auth, "api_key", None),
                resource_type="mcp_tool",
                resource_id=namespaced_tool_name,
                request_id=request_id,
                correlation_id=request_id,
                ip=request_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                status="error" if bool(result_payload.get("is_error")) else "success",
                latency_ms=int((perf_counter() - request_start) * 1000),
                metadata={
                    "server_key": server_key,
                    "tool_name": namespaced_tool_name,
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                },
            ),
            payloads=[
                AuditPayloadInput(kind="request", content_json={"arguments": arguments}),
                AuditPayloadInput(kind="response", content_json=result_payload),
            ],
            critical=False,
        )

    def _emit_tool_audit_failure_event(
        self,
        *,
        request: Request,
        auth: Any,
        namespaced_tool_name: str,
        server_key: str,
        scope_type: str | None,
        scope_id: str | None,
        arguments: dict[str, Any],
        request_start: float,
        error: Exception,
    ) -> None:
        audit_service: AuditService | None = getattr(request.app.state, "audit_service", None)
        if audit_service is None:
            return
        request_id = request.headers.get("x-request-id")
        audit_service.record_event(
            AuditEventInput(
                action=AuditAction.MCP_TOOL_CALL.value,
                organization_id=getattr(auth, "organization_id", None),
                actor_type="api_key",
                actor_id=getattr(auth, "user_id", None) or getattr(auth, "api_key", None),
                api_key=getattr(auth, "api_key", None),
                resource_type="mcp_tool",
                resource_id=namespaced_tool_name,
                request_id=request_id,
                correlation_id=request_id,
                ip=request_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                status="error",
                latency_ms=int((perf_counter() - request_start) * 1000),
                error_type=error.__class__.__name__,
                metadata={
                    "server_key": server_key,
                    "tool_name": namespaced_tool_name,
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                },
            ),
            payloads=[AuditPayloadInput(kind="request", content_json={"arguments": arguments})],
            critical=False,
        )
