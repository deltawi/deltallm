from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import ValidationError

from src.audit.actions import AuditAction
from src.audit.delivery import AuditDeliveryClass
from src.guardrails.middleware import GuardrailMiddleware
from src.models.errors import (
    InvalidRequestError,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
)
from src.models.requests import (
    AssistantChatMessage,
    ChatCompletionRequest,
    FunctionToolDefinition,
    MCPToolDefinition,
    ToolChatMessage,
)
from src.models.responses import UserAPIKeyAuth
from src.services.audit.audit_service import (
    AuditEventInput,
    AuditPayloadInput,
    AuditService,
    enqueue_audit_event,
    require_audit_service,
)

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
from .bundle_runtime import list_bundle_tools, resolve_visible_bundle
from .gateway import MCPGatewayService
from .hop_usage import add_hop_usage
from .metrics import record_mcp_tool_search
from .tool_search import (
    DEFAULT_SEARCH_LIMIT,
    MAX_LOADED_TOOLS,
    SEARCH_TOOL_NAME,
    SearchableTool,
    ToolSearchIndex,
    search_tool_definition,
)

ALL_SERVERS = "*"


@dataclass(frozen=True)
class ResolvedMCPTool:
    server_key: str
    original_name: str
    namespaced_name: str
    description: str | None
    input_schema: dict[str, Any]
    scope_type: str | None = None
    scope_id: str | None = None
    # The real `server.tool` name when a bundle shows the tool under another name.
    call_name: str | None = None

    @property
    def gateway_name(self) -> str:
        return self.call_name or self.namespaced_name


@dataclass(frozen=True, slots=True)
class MCPRequestContext:
    request_headers: Mapping[str, str]
    request_id: str | None
    correlation_id: str | None
    client_ip: str | None
    user_agent: str | None


@dataclass(frozen=True, slots=True)
class MCPToolSet:
    """The MCP tools of one request: all tools it may call, and the optional search index."""

    resolved: dict[str, ResolvedMCPTool]
    search: ToolSearchIndex | None = None
    searchable: frozenset[str] = frozenset()

    def owns(self, tool_name: str) -> bool:
        if tool_name == SEARCH_TOOL_NAME:
            return self.search is not None
        return tool_name in self.resolved


def _function_tool(tool: ResolvedMCPTool) -> FunctionToolDefinition:
    return FunctionToolDefinition(
        type="function",
        function={
            "name": tool.namespaced_name,
            "description": tool.description,
            "parameters": dict(tool.input_schema or {"type": "object"}),
        },
    )


def _with_loaded_tools(
    tools: list[Any], loaded: list[ResolvedMCPTool], searchable: frozenset[str]
) -> list[Any] | None:
    if not loaded:
        return None
    names = {
        tool.function.get("name") for tool in tools if isinstance(tool, FunctionToolDefinition)
    }
    # Searches can add at most MAX_LOADED_TOOLS tools to one request.
    budget = MAX_LOADED_TOOLS - len(names & searchable)
    out = list(tools)
    for tool in loaded:
        if budget <= 0:
            break
        if tool.namespaced_name in names:
            continue
        names.add(tool.namespaced_name)
        out.append(_function_tool(tool))
        budget -= 1
    return out


MCPChatCall = Callable[
    [ChatCompletionRequest],
    Awaitable[tuple[dict[str, Any], float]],
]


def chat_request_has_mcp_tools(payload: ChatCompletionRequest) -> bool:
    return any(isinstance(tool, MCPToolDefinition) for tool in payload.tools or [])


class MCPChatOrchestrator:
    def __init__(
        self,
        gateway: MCPGatewayService,
        *,
        audit_service: AuditService | None = None,
        max_mcp_tool_hops: int = 4,
        max_mcp_tools_per_turn: int = 8,
    ) -> None:
        self.gateway = gateway
        self.audit_service = audit_service
        self.max_mcp_tool_hops = max(1, int(max_mcp_tool_hops))
        self.max_mcp_tools_per_turn = max(1, int(max_mcp_tools_per_turn))

    async def prepare_payload(
        self,
        auth: UserAPIKeyAuth,
        payload: ChatCompletionRequest,
    ) -> tuple[ChatCompletionRequest, MCPToolSet]:
        requested_mcp_tools = [
            tool for tool in payload.tools or [] if isinstance(tool, MCPToolDefinition)
        ]
        if not requested_mcp_tools:
            return payload, MCPToolSet(resolved={})

        visible = await self.gateway.list_visible_tools(auth)
        translated: list[FunctionToolDefinition] = [
            tool
            if isinstance(tool, FunctionToolDefinition)
            else FunctionToolDefinition.model_validate(tool.model_dump(mode="python"))
            for tool in (payload.tools or [])
            if isinstance(tool, FunctionToolDefinition)
        ]
        resolved_tools: dict[str, ResolvedMCPTool] = {}
        exposed: set[str] = set()
        searchable: dict[str, ResolvedMCPTool] = {}

        client_function_names = {tool.function.get("name") for tool in translated}

        for definition in requested_mcp_tools:
            if definition.server == ALL_SERVERS and not definition.tool_search:
                raise InvalidRequestError(
                    message=f"MCP server '{ALL_SERVERS}' needs `tool_search: true`"
                )
            matched = await self._match_definition_tools(
                auth, definition, visible, client_function_names
            )
            if not matched:
                raise InvalidRequestError(
                    message=f"No visible MCP tools are available for server '{definition.server}'"
                )
            kept = 0
            for name, tool in matched:
                existing = resolved_tools.get(name)
                if existing is not None and existing.gateway_name != tool.namespaced_name:
                    raise InvalidRequestError(
                        message=f"Two MCP tools in this request use the name '{name}'"
                    )
                if name in exposed:
                    kept += 1
                    continue
                if existing is None:
                    requires_manual_approval = False
                    if hasattr(self.gateway, "tool_requires_manual_approval"):
                        requires_manual_approval = await self.gateway.tool_requires_manual_approval(
                            auth,
                            server_key=tool.server_key,
                            tool_name=tool.original_name,
                        )
                    if requires_manual_approval:
                        if definition.tool_search:
                            # Search covers a whole catalog; leave out what chat cannot run.
                            continue
                        raise InvalidRequestError(
                            message=(
                                "MCP tools that require manual approval are not supported on chat/responses yet. "
                                "Use the /mcp tools/call flow and retry after approval."
                            )
                        )
                    resolved_tools[name] = ResolvedMCPTool(
                        server_key=tool.server_key,
                        original_name=tool.original_name,
                        namespaced_name=name,
                        description=tool.description,
                        input_schema=dict(tool.input_schema or {}),
                        scope_type=getattr(tool, "scope_type", None),
                        scope_id=getattr(tool, "scope_id", None),
                        call_name=tool.namespaced_name if name != tool.namespaced_name else None,
                    )
                resolved = resolved_tools[name]
                kept += 1
                if definition.tool_search:
                    searchable[resolved.namespaced_name] = resolved
                    continue
                exposed.add(resolved.namespaced_name)
                searchable.pop(resolved.namespaced_name, None)
                translated.append(_function_tool(resolved))
            if not kept and definition.tool_search:
                # Do not run the request without the tools that the caller asked for.
                raise InvalidRequestError(
                    message=(
                        f"All MCP tools for server '{definition.server}' require manual approval. "
                        "Use the /mcp tools/call flow and retry after approval."
                    )
                )

        search = None
        if searchable:
            if any(tool.function.get("name") == SEARCH_TOOL_NAME for tool in translated):
                raise InvalidRequestError(
                    message=f"The function name '{SEARCH_TOOL_NAME}' is reserved when `tool_search` is on"
                )
            search = ToolSearchIndex(
                [
                    SearchableTool(tool.namespaced_name, tool.description, tool.input_schema)
                    for tool in searchable.values()
                ]
            )
            translated.append(
                FunctionToolDefinition(type="function", function=search_tool_definition())
            )
        return payload.model_copy(update={"tools": translated}), MCPToolSet(
            resolved=resolved_tools, search=search, searchable=frozenset(searchable)
        )

    async def _match_definition_tools(
        self,
        auth: UserAPIKeyAuth,
        definition: MCPToolDefinition,
        visible: list[Any],
        client_function_names: set[Any],
    ) -> list[tuple[str, Any]]:
        """Return (name for the model, visible tool) pairs for one MCP tool entry."""
        allowed = set(definition.allowed_tools) if definition.allowed_tools is not None else None
        if definition.server == ALL_SERVERS or any(
            tool.server_key == definition.server for tool in visible
        ):
            return [
                (tool.namespaced_name, tool)
                for tool in visible
                if definition.server in (ALL_SERVERS, tool.server_key)
                and (
                    allowed is None
                    or tool.original_name in allowed
                    # Across all servers, a namespaced name selects one server's tool.
                    or (definition.server == ALL_SERVERS and tool.namespaced_name in allowed)
                )
            ]
        # Not a server that the caller can see: look for a bundle. A bundle that
        # the caller cannot see gives the same error as an unknown server.
        if not isinstance(self.gateway, MCPGatewayService):
            return []
        bundle = resolve_visible_bundle(self.gateway, auth, definition.server)
        if bundle is None:
            return []
        matched: list[tuple[str, Any]] = []
        for item in await list_bundle_tools(self.gateway, auth, bundle, visible_tools=visible):
            if allowed is not None and item.exposed_name not in allowed:
                continue
            if item.exposed_name in client_function_names:
                raise InvalidRequestError(
                    message=f"The function name '{item.exposed_name}' is also a tool of MCP bundle '{bundle.bundle_key}'"
                )
            matched.append((item.exposed_name, item.tool))
        return matched

    async def execute(
        self,
        *,
        request_context: MCPRequestContext,
        auth: UserAPIKeyAuth,
        payload: ChatCompletionRequest,
        execute_chat_call: MCPChatCall,
        guardrail_middleware: GuardrailMiddleware,
    ) -> tuple[dict[str, Any], float]:
        translated_payload, tool_set = await self.prepare_payload(auth, payload)
        if not tool_set.resolved:
            return await execute_chat_call(translated_payload)

        current_payload = translated_payload
        total_api_latency_ms = 0.0
        total_usage: dict[str, Any] | None = None

        for _ in range(self.max_mcp_tool_hops + 1):
            response_payload, api_latency_ms = await execute_chat_call(current_payload)
            total_api_latency_ms += api_latency_ms
            total_usage = add_hop_usage(total_usage, response_payload.get("usage"))
            tool_calls = self._extract_tool_calls(response_payload)
            if not self.gateway_owns_tool_calls(tool_calls, tool_set):
                if total_usage is not None:
                    response_payload = {**response_payload, "usage": total_usage}
                return response_payload, total_api_latency_ms

            current_payload = await self.next_hop_payload(
                current_payload,
                assistant_response=response_payload,
                request_context=request_context,
                auth=auth,
                guardrail_middleware=guardrail_middleware,
                tool_set=tool_set,
                tool_calls=tool_calls,
            )

        raise InvalidRequestError(message="MCP tool execution exceeded the configured hop limit")

    async def next_hop_payload(
        self,
        current_payload: ChatCompletionRequest,
        *,
        assistant_response: dict[str, Any],
        request_context: MCPRequestContext,
        auth: UserAPIKeyAuth,
        guardrail_middleware: GuardrailMiddleware,
        tool_set: MCPToolSet,
        tool_calls: list[dict[str, Any]],
    ) -> ChatCompletionRequest:
        """Run one turn of tool calls and build the request for the next model call."""
        messages = list(current_payload.messages)
        messages.append(self.assistant_message_from_response(assistant_response))
        tool_messages: list[ToolChatMessage] = []
        mcp_calls: list[dict[str, Any]] = []
        loaded: list[ResolvedMCPTool] = []
        declared = {
            tool.function.get("name")
            for tool in current_payload.tools or []
            if isinstance(tool, FunctionToolDefinition)
        }
        free_slots = [MAX_LOADED_TOOLS - len(declared & tool_set.searchable)]
        # Keep the provider order of tool results; search calls run in place.
        for position, tool_call in enumerate(tool_calls):
            search = tool_set.search
            if search is not None and self._tool_name_from_call(tool_call) == SEARCH_TOOL_NAME:
                if mcp_calls:
                    tool_messages.extend(
                        await self.run_tool_calls(
                            request_context=request_context,
                            auth=auth,
                            guardrail_middleware=guardrail_middleware,
                            resolved_tools=tool_set.resolved,
                            tool_calls=mcp_calls,
                        )
                    )
                    mcp_calls = []
                message, found = self._run_tool_search(
                    search,
                    tool_set,
                    tool_call,
                    declared=declared,
                    free_slots=free_slots,
                    position=position,
                )
                tool_messages.append(message)
                loaded.extend(found)
            else:
                mcp_calls.append(tool_call)
        if mcp_calls:
            tool_messages.extend(
                await self.run_tool_calls(
                    request_context=request_context,
                    auth=auth,
                    guardrail_middleware=guardrail_middleware,
                    resolved_tools=tool_set.resolved,
                    tool_calls=mcp_calls,
                )
            )
        messages.extend(tool_messages)
        update: dict[str, Any] = {"messages": messages}
        tools = _with_loaded_tools(current_payload.tools or [], loaded, tool_set.searchable)
        if tools is not None:
            update["tools"] = tools
        return current_payload.model_copy(update=update)

    def _run_tool_search(
        self,
        search: ToolSearchIndex,
        tool_set: MCPToolSet,
        tool_call: dict[str, Any],
        *,
        declared: set[str],
        free_slots: list[int],
        position: int,
    ) -> tuple[ToolChatMessage, list[ResolvedMCPTool]]:
        arguments = self._arguments_from_tool_call(tool_call)
        query = arguments.get("query")
        limit = arguments.get("limit", DEFAULT_SEARCH_LIMIT)
        if not isinstance(query, str):
            query = ""
        if isinstance(limit, float) and limit.is_integer():
            limit = int(limit)
        if not isinstance(limit, int) or isinstance(limit, bool):
            limit = DEFAULT_SEARCH_LIMIT
        hits = []
        found: list[ResolvedMCPTool] = []
        for hit in search.search(query, limit=limit):
            if hit.name not in declared:
                # Only list tools that the next request really declares.
                if free_slots[0] <= 0:
                    continue
                free_slots[0] -= 1
                declared.add(hit.name)
                found.append(tool_set.resolved[hit.name])
            hits.append(hit)
        record_mcp_tool_search(result_count=len(hits))
        content = json.dumps(
            {
                "tools": [{"name": hit.name, "description": hit.description} for hit in hits],
                "note": "These tools are available in your next turn."
                if hits
                else "No tools match. Try other words.",
            },
            separators=(",", ":"),
        )
        message = ToolChatMessage(
            role="tool",
            content=content,
            tool_call_id=str(tool_call.get("id") or f"{SEARCH_TOOL_NAME}_{position}"),
        )
        return message, found

    def gateway_owns_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        tool_set: MCPToolSet,
    ) -> bool:
        """Return True when every call is an MCP tool that the gateway must run.

        A turn without tool calls, or with a client function tool, goes back to
        the client unchanged.
        """
        if not tool_calls:
            return False
        if len(tool_calls) > self.max_mcp_tools_per_turn:
            raise InvalidRequestError(
                message=f"MCP tool execution limit exceeded: received {len(tool_calls)} tool calls, limit is {self.max_mcp_tools_per_turn}"
            )
        return all(tool_set.owns(self._tool_name_from_call(tool_call)) for tool_call in tool_calls)

    async def run_tool_calls(
        self,
        *,
        request_context: MCPRequestContext,
        auth: UserAPIKeyAuth,
        guardrail_middleware: GuardrailMiddleware,
        resolved_tools: Mapping[str, ResolvedMCPTool],
        tool_calls: list[dict[str, Any]],
    ) -> list[ToolChatMessage]:
        tool_messages: list[ToolChatMessage] = []
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
            tool_operation_id = str(uuid4())
            await self._emit_tool_audit_attempt_event(
                request_context=request_context,
                auth=auth,
                namespaced_tool_name=resolved.gateway_name,
                server_key=resolved.server_key,
                scope_type=resolved.scope_type,
                scope_id=resolved.scope_id,
                arguments=guarded_arguments,
                operation_id=tool_operation_id,
            )
            try:
                result = await self.gateway.call_tool(
                    auth,
                    namespaced_tool_name=resolved.gateway_name,
                    arguments=guarded_arguments,
                    request_headers=dict(request_context.request_headers),
                    request_id=request_context.request_id,
                    correlation_id=request_context.correlation_id,
                )
            except MCPToolNotFoundError as exc:
                await self._emit_tool_audit_failure_event(
                    request_context=request_context,
                    auth=auth,
                    namespaced_tool_name=resolved.gateway_name,
                    server_key=resolved.server_key,
                    scope_type=resolved.scope_type,
                    scope_id=resolved.scope_id,
                    arguments=guarded_arguments,
                    request_start=tool_started,
                    error=exc,
                    operation_id=tool_operation_id,
                )
                raise InvalidRequestError(message=str(exc)) from exc
            except MCPApprovalRequiredError as exc:
                await self._emit_tool_audit_failure_event(
                    request_context=request_context,
                    auth=auth,
                    namespaced_tool_name=resolved.gateway_name,
                    server_key=resolved.server_key,
                    scope_type=resolved.scope_type,
                    scope_id=resolved.scope_id,
                    arguments=guarded_arguments,
                    request_start=tool_started,
                    error=exc,
                    operation_id=tool_operation_id,
                )
                raise InvalidRequestError(
                    message=(
                        "MCP tools that require manual approval are not supported on chat/responses yet. "
                        "Use the /mcp tools/call flow and retry after approval."
                    ),
                ) from exc
            except (MCPAccessDeniedError, MCPPolicyDeniedError) as exc:
                await self._emit_tool_audit_failure_event(
                    request_context=request_context,
                    auth=auth,
                    namespaced_tool_name=resolved.gateway_name,
                    server_key=resolved.server_key,
                    scope_type=resolved.scope_type,
                    scope_id=resolved.scope_id,
                    arguments=guarded_arguments,
                    request_start=tool_started,
                    error=exc,
                    operation_id=tool_operation_id,
                )
                raise PermissionDeniedError(message=str(exc)) from exc
            except MCPRateLimitError as exc:
                await self._emit_tool_audit_failure_event(
                    request_context=request_context,
                    auth=auth,
                    namespaced_tool_name=resolved.gateway_name,
                    server_key=resolved.server_key,
                    scope_type=resolved.scope_type,
                    scope_id=resolved.scope_id,
                    arguments=guarded_arguments,
                    request_start=tool_started,
                    error=exc,
                    operation_id=tool_operation_id,
                )
                raise RateLimitError(message=str(exc), retry_after=exc.retry_after) from exc
            except (
                MCPTransportError,
                MCPInvalidResponseError,
                MCPToolTimeoutError,
                MCPError,
            ) as exc:
                await self._emit_tool_audit_failure_event(
                    request_context=request_context,
                    auth=auth,
                    namespaced_tool_name=resolved.gateway_name,
                    server_key=resolved.server_key,
                    scope_type=resolved.scope_type,
                    scope_id=resolved.scope_id,
                    arguments=guarded_arguments,
                    request_start=tool_started,
                    error=exc,
                    operation_id=tool_operation_id,
                )
                raise ServiceUnavailableError(message=str(exc)) from exc

            guarded_result = await self._run_tool_output_guardrails(
                guardrail_middleware=guardrail_middleware,
                auth=auth,
                server_key=resolved.server_key,
                tool_name=resolved.original_name,
                result=result,
            )
            await self._emit_tool_audit_event(
                request_context=request_context,
                auth=auth,
                namespaced_tool_name=resolved.gateway_name,
                server_key=resolved.server_key,
                scope_type=resolved.scope_type,
                scope_id=resolved.scope_id,
                arguments=guarded_arguments,
                result_payload=guarded_result,
                request_start=tool_started,
                operation_id=tool_operation_id,
            )
            tool_messages.append(
                ToolChatMessage(
                    role="tool",
                    content=self._tool_message_content(guarded_result),
                    tool_call_id=str(tool_call.get("id") or tool_name),
                )
            )

        return tool_messages

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
    def assistant_message_from_response(
        response_payload: dict[str, Any],
    ) -> AssistantChatMessage:
        choice = (
            ((response_payload.get("choices") or [{}])[0])
            if isinstance(response_payload.get("choices"), list)
            else {}
        )
        message = choice.get("message") if isinstance(choice, dict) else {}
        if not isinstance(message, dict):
            message = {}
        try:
            return AssistantChatMessage.model_validate(
                {
                    "role": "assistant",
                    **{
                        key: message[key]
                        for key in (
                            "content",
                            "name",
                            "tool_calls",
                            "reasoning_content",
                            "reasoning_details",
                        )
                        if key in message
                    },
                }
            )
        except ValidationError:
            raise ServiceUnavailableError(
                message="Provider returned invalid assistant history"
            ) from None

    @staticmethod
    def _tool_name_from_call(tool_call: dict[str, Any]) -> str:
        function = tool_call.get("function")
        if not isinstance(function, dict):
            raise InvalidRequestError(message="Provider returned an invalid tool call payload")
        name = str(function.get("name") or "").strip()
        if not name:
            raise InvalidRequestError(
                message="Provider returned a tool call without a function name"
            )
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
                raise InvalidRequestError(
                    message="Provider returned invalid JSON tool arguments"
                ) from exc
            if not isinstance(parsed, dict):
                raise InvalidRequestError(message="Provider returned non-object tool arguments")
            return parsed
        raise InvalidRequestError(message="Provider returned unsupported tool arguments")

    async def _run_tool_input_guardrails(
        self,
        *,
        guardrail_middleware: GuardrailMiddleware,
        auth: UserAPIKeyAuth,
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
            raise InvalidRequestError(
                message="Guardrail mutated MCP tool arguments into an invalid shape"
            )
        return guarded_arguments

    async def _run_tool_output_guardrails(
        self,
        *,
        guardrail_middleware: GuardrailMiddleware,
        auth: UserAPIKeyAuth,
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
            raise InvalidRequestError(
                message="Guardrail mutated MCP tool output into an invalid shape"
            )
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

    async def _emit_tool_audit_event(
        self,
        *,
        request_context: MCPRequestContext,
        auth: UserAPIKeyAuth,
        namespaced_tool_name: str,
        server_key: str,
        scope_type: str | None,
        scope_id: str | None,
        arguments: dict[str, Any],
        result_payload: dict[str, Any],
        request_start: float,
        operation_id: str,
    ) -> None:
        audit_service = self.audit_service
        if audit_service is None:
            return
        request_id = request_context.request_id
        await enqueue_audit_event(
            audit_service,
            AuditEventInput(
                action=AuditAction.MCP_TOOL_CALL.value,
                organization_id=getattr(auth, "organization_id", None),
                actor_type="api_key",
                actor_id=getattr(auth, "user_id", None) or getattr(auth, "api_key", None),
                api_key=getattr(auth, "api_key", None),
                resource_type="mcp_tool",
                resource_id=namespaced_tool_name,
                request_id=request_id,
                correlation_id=request_context.correlation_id,
                ip=request_context.client_ip,
                user_agent=request_context.user_agent,
                status="error" if bool(result_payload.get("is_error")) else "success",
                latency_ms=int((perf_counter() - request_start) * 1000),
                metadata={
                    "operation_id": operation_id,
                    "phase": "outcome",
                    "server_key": server_key,
                    "tool_name": namespaced_tool_name,
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                },
                event_id=self._tool_audit_event_id(operation_id, "outcome"),
            ),
            payloads=[
                AuditPayloadInput(kind="request", content_json={"arguments": arguments}),
                AuditPayloadInput(kind="response", content_json=result_payload),
            ],
            delivery_class=AuditDeliveryClass.BEST_EFFORT,
        )

    async def _emit_tool_audit_failure_event(
        self,
        *,
        request_context: MCPRequestContext,
        auth: UserAPIKeyAuth,
        namespaced_tool_name: str,
        server_key: str,
        scope_type: str | None,
        scope_id: str | None,
        arguments: dict[str, Any],
        request_start: float,
        error: Exception,
        operation_id: str,
    ) -> None:
        audit_service = self.audit_service
        if audit_service is None:
            return
        request_id = request_context.request_id
        await enqueue_audit_event(
            audit_service,
            AuditEventInput(
                action=AuditAction.MCP_TOOL_CALL.value,
                organization_id=getattr(auth, "organization_id", None),
                actor_type="api_key",
                actor_id=getattr(auth, "user_id", None) or getattr(auth, "api_key", None),
                api_key=getattr(auth, "api_key", None),
                resource_type="mcp_tool",
                resource_id=namespaced_tool_name,
                request_id=request_id,
                correlation_id=request_context.correlation_id,
                ip=request_context.client_ip,
                user_agent=request_context.user_agent,
                status="error",
                latency_ms=int((perf_counter() - request_start) * 1000),
                error_type=error.__class__.__name__,
                metadata={
                    "operation_id": operation_id,
                    "phase": "outcome",
                    "server_key": server_key,
                    "tool_name": namespaced_tool_name,
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                },
                event_id=self._tool_audit_event_id(operation_id, "outcome"),
            ),
            payloads=[AuditPayloadInput(kind="request", content_json={"arguments": arguments})],
            delivery_class=AuditDeliveryClass.BEST_EFFORT,
        )

    async def _emit_tool_audit_attempt_event(
        self,
        *,
        request_context: MCPRequestContext,
        auth: UserAPIKeyAuth,
        namespaced_tool_name: str,
        server_key: str,
        scope_type: str | None,
        scope_id: str | None,
        arguments: dict[str, Any],
        operation_id: str,
    ) -> None:
        audit_service = require_audit_service(self.audit_service)
        request_id = request_context.request_id
        await enqueue_audit_event(
            audit_service,
            AuditEventInput(
                action=AuditAction.MCP_TOOL_CALL.value,
                organization_id=getattr(auth, "organization_id", None),
                actor_type="api_key",
                actor_id=getattr(auth, "user_id", None) or getattr(auth, "api_key", None),
                api_key=getattr(auth, "api_key", None),
                resource_type="mcp_tool",
                resource_id=namespaced_tool_name,
                request_id=request_id,
                correlation_id=request_context.correlation_id,
                ip=request_context.client_ip,
                user_agent=request_context.user_agent,
                status="attempted",
                metadata={
                    "operation_id": operation_id,
                    "phase": "attempt",
                    "server_key": server_key,
                    "tool_name": namespaced_tool_name,
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                },
                event_id=self._tool_audit_event_id(operation_id, "attempt"),
            ),
            payloads=[AuditPayloadInput(kind="request", content_json={"arguments": arguments})],
            delivery_class=AuditDeliveryClass.REQUIRED,
        )

    @staticmethod
    def _tool_audit_event_id(operation_id: str, phase: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"deltallm:mcp-tool:{operation_id}:{phase}"))
