from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeVar

from src.db.mcp import MCPServerBindingRecord, MCPServerRecord, MCPToolPolicyRecord
from src.models.responses import UserAPIKeyAuth
from src.services.runtime_scopes import resolve_runtime_scope_context

from .approvals import MCPApprovalService
from .capabilities import NamespacedTool, parse_namespaced_tool_name
from .exceptions import (
    MCPAccessDeniedError,
    MCPApprovalDeniedError,
    MCPApprovalRequiredError,
    MCPPolicyDeniedError,
    MCPRateLimitError,
    MCPToolNotFoundError,
    MCPToolTimeoutError,
)
from .governance import MCPGovernanceService
from .metrics import record_mcp_tool_call, record_mcp_tools_list
from .models import MCPBindingResolution, MCPToolCallResult
from .policy import MCPToolPolicyEnforcer, resolve_policy_timeout_ms
from .result_cache import MCPToolResultCache
from .registry import MCPRegistryService, server_record_to_config
from .transport_http import StreamableHTTPMCPClient

_ScopeRecordT = TypeVar("_ScopeRecordT", MCPServerBindingRecord, MCPToolPolicyRecord)


@dataclass(frozen=True)
class VisibleMCPServer:
    server: MCPServerRecord
    binding: MCPBindingResolution
    tool_names: tuple[str, ...]


@dataclass(frozen=True)
class MCPResolvedToolScope:
    server_key: str
    tool_name: str
    scope_type: str
    scope_id: str


class MCPGatewayService:
    def __init__(
        self,
        registry: MCPRegistryService,
        transport_client: StreamableHTTPMCPClient,
        policy_enforcer: MCPToolPolicyEnforcer | None = None,
        result_cache: MCPToolResultCache | None = None,
        approval_service: MCPApprovalService | None = None,
        governance_service: MCPGovernanceService | None = None,
    ) -> None:
        self.registry = registry
        self.transport_client = transport_client
        self.policy_enforcer = policy_enforcer
        self.result_cache = result_cache
        self.approval_service = approval_service
        self.governance_service = governance_service

    async def list_visible_tools(self, auth: UserAPIKeyAuth) -> list[NamespacedTool]:
        started = perf_counter()
        visible_servers = await self.list_visible_servers(auth)
        tools: list[NamespacedTool] = []
        try:
            for visible in visible_servers:
                tools.extend(await self._filtered_tools_for_server(auth, visible))
        except Exception:
            record_mcp_tools_list(
                server_count=len(visible_servers),
                tool_count=0,
                success=False,
                latency_ms=int((perf_counter() - started) * 1000),
            )
            raise
        record_mcp_tools_list(
            server_count=len(visible_servers),
            tool_count=len(tools),
            success=True,
            latency_ms=int((perf_counter() - started) * 1000),
        )
        return tools

    async def call_tool(
        self,
        auth: UserAPIKeyAuth,
        *,
        namespaced_tool_name: str,
        arguments: dict[str, Any] | None,
        request_headers: dict[str, str] | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> MCPToolCallResult:
        started = perf_counter()
        server_key, tool_name = parse_namespaced_tool_name(namespaced_tool_name)
        visible = await self._visible_server_by_key(auth, server_key)
        if visible is None:
            record_mcp_tool_call(
                server_key=server_key,
                tool_name=tool_name,
                success=False,
                latency_ms=int((perf_counter() - started) * 1000),
            )
            raise MCPToolNotFoundError(f"Unknown MCP tool '{namespaced_tool_name}'")

        allowed_tools = set(visible.tool_names)
        if tool_name not in allowed_tools:
            record_mcp_tool_call(
                server_key=server_key,
                tool_name=tool_name,
                success=False,
                latency_ms=int((perf_counter() - started) * 1000),
            )
            raise MCPToolNotFoundError(f"Unknown MCP tool '{namespaced_tool_name}'")

        policy = await self._effective_tool_policy(auth, visible.server.mcp_server_id, tool_name)
        if policy is not None:
            if not policy.enabled:
                record_mcp_tool_call(
                    server_key=server_key,
                    tool_name=tool_name,
                    success=False,
                    latency_ms=int((perf_counter() - started) * 1000),
                )
                raise MCPPolicyDeniedError(f"MCP tool '{namespaced_tool_name}' is disabled by policy")
            if policy.require_approval and policy.require_approval != "never":
                if policy.require_approval != "manual":
                    record_mcp_tool_call(
                        server_key=server_key,
                        tool_name=tool_name,
                        success=False,
                        latency_ms=int((perf_counter() - started) * 1000),
                    )
                    raise MCPPolicyDeniedError(f"MCP tool '{namespaced_tool_name}' uses an unsupported approval mode")
                if self.approval_service is None:
                    record_mcp_tool_call(
                        server_key=server_key,
                        tool_name=tool_name,
                        success=False,
                        latency_ms=int((perf_counter() - started) * 1000),
                    )
                    raise MCPPolicyDeniedError(f"MCP tool '{namespaced_tool_name}' requires approval, but the approval service is unavailable")
                approval = await self.approval_service.authorize_execution(
                        server=server_record_to_config(visible.server),
                        tool_name=tool_name,
                        policy=policy,
                        auth=auth,
                        arguments=arguments,
                        request_headers=request_headers,
                        request_id=request_id,
                        correlation_id=correlation_id,
                    )
                if approval is None:
                    record_mcp_tool_call(
                        server_key=server_key,
                        tool_name=tool_name,
                        success=False,
                        latency_ms=int((perf_counter() - started) * 1000),
                    )
                    raise MCPPolicyDeniedError(f"MCP tool '{namespaced_tool_name}' requires approval, but no approval record could be created")
                if approval.status == "approved":
                    pass
                elif approval.status == "rejected":
                    record_mcp_tool_call(
                        server_key=server_key,
                        tool_name=tool_name,
                        success=False,
                        latency_ms=int((perf_counter() - started) * 1000),
                    )
                    raise MCPApprovalDeniedError(
                        f"MCP tool '{namespaced_tool_name}' approval was rejected",
                        approval_request_id=approval.approval_request.mcp_approval_request_id,
                    )
                else:
                    record_mcp_tool_call(
                        server_key=server_key,
                        tool_name=tool_name,
                        success=False,
                        latency_ms=int((perf_counter() - started) * 1000),
                    )
                    raise MCPApprovalRequiredError(
                        f"MCP tool '{namespaced_tool_name}' requires approval",
                        approval_request_id=approval.approval_request.mcp_approval_request_id,
                    )

        lease = None
        cache_ttl = policy.result_cache_ttl_seconds if policy is not None and policy.result_cache_ttl_seconds else 0
        policy_timeout_ms = resolve_policy_timeout_ms(policy)
        server_config = server_record_to_config(visible.server)
        try:
            if self.policy_enforcer is not None:
                lease = await self.policy_enforcer.acquire(
                    server_key=server_key,
                    tool_name=tool_name,
                    policy=policy,
                )
            async def _execute_tool_call() -> MCPToolCallResult:
                result = None
                if cache_ttl > 0 and self.result_cache is not None:
                    result = await self.result_cache.get(
                        server=server_config,
                        tool_name=tool_name,
                        arguments=arguments,
                        request_headers=request_headers,
                        auth_api_key=getattr(auth, "api_key", None),
                    )
                if result is None:
                    result = await self.transport_client.call_tool(
                        server_config,
                        tool_name=tool_name,
                        arguments=arguments or {},
                        request_headers=request_headers,
                    )
                    if cache_ttl > 0 and self.result_cache is not None:
                        await self.result_cache.set(
                            server=server_config,
                            tool_name=tool_name,
                            arguments=arguments,
                            request_headers=request_headers,
                            auth_api_key=getattr(auth, "api_key", None),
                            result=result,
                            ttl_seconds=cache_ttl,
                        )
                return result

            if policy_timeout_ms is not None:
                try:
                    async with asyncio.timeout(policy_timeout_ms / 1000):
                        result = await _execute_tool_call()
                except TimeoutError as exc:
                    raise MCPToolTimeoutError(
                        f"MCP tool '{namespaced_tool_name}' exceeded the policy execution limit of {policy_timeout_ms} ms",
                        timeout_ms=policy_timeout_ms,
                    ) from exc
            else:
                result = await _execute_tool_call()
        except (MCPRateLimitError, MCPToolTimeoutError):
            record_mcp_tool_call(
                server_key=server_key,
                tool_name=tool_name,
                success=False,
                latency_ms=int((perf_counter() - started) * 1000),
                metadata={"scope_type": visible.binding.scope_type, "scope_id": visible.binding.scope_id},
            )
            raise
        except Exception:
            record_mcp_tool_call(
                server_key=server_key,
                tool_name=tool_name,
                success=False,
                latency_ms=int((perf_counter() - started) * 1000),
                metadata={"scope_type": visible.binding.scope_type, "scope_id": visible.binding.scope_id},
            )
            raise
        finally:
            if self.policy_enforcer is not None:
                await self.policy_enforcer.release(lease)
        record_mcp_tool_call(
            server_key=server_key,
            tool_name=tool_name,
            success=not result.is_error,
            latency_ms=int((perf_counter() - started) * 1000),
            metadata={"scope_type": visible.binding.scope_type, "scope_id": visible.binding.scope_id},
        )
        result_metadata = dict(result.metadata or {})
        result_metadata.update(
            {
                "server_key": server_key,
                "tool_name": namespaced_tool_name,
                "scope_type": visible.binding.scope_type,
                "scope_id": visible.binding.scope_id,
            }
        )
        return MCPToolCallResult(
            content=list(result.content),
            structured_content=dict(result.structured_content) if isinstance(result.structured_content, dict) else result.structured_content,
            is_error=result.is_error,
            metadata=result_metadata,
        )

    async def list_visible_servers(self, auth: UserAPIKeyAuth) -> list[VisibleMCPServer]:
        scope_context = resolve_runtime_scope_context(auth)
        if scope_context.is_master_key:
            servers = (
                self.governance_service.list_enabled_servers()
                if self.governance_service is not None
                else (await self.registry.list_servers(enabled=True, limit=500, offset=0))[0]
            )
            visible: list[VisibleMCPServer] = []
            for server in servers:
                tools = await self.registry.list_namespaced_tools(server)
                visible.append(
                    VisibleMCPServer(
                        server=server,
                        binding=MCPBindingResolution(
                            server_id=server.mcp_server_id,
                            server_key=server.server_key,
                            scope_type="master_key",
                            scope_id="master_key",
                            allowed_tool_names=None,
                        ),
                        tool_names=tuple(tool.original_name for tool in tools),
                    )
                )
            return visible

        bindings = (
            self.governance_service.resolve_binding_resolutions(auth)
            if self.governance_service is not None
            else await self.registry.list_effective_bindings(scopes=list(scope_context.scope_chain))
        )
        visible_servers: list[VisibleMCPServer] = []
        if self.governance_service is not None:
            resolved_bindings = list(bindings)
        else:
            grouped: dict[str, list[MCPServerBindingRecord]] = defaultdict(list)
            for binding in bindings:
                grouped[binding.mcp_server_id].append(binding)
            resolved_bindings = [
                self._select_binding(server_bindings, scope_order=scope_context.scope_chain)
                for server_bindings in grouped.values()
            ]
        for binding in resolved_bindings:
            server_id = binding.server_id
            server = (
                self.governance_service.get_server(server_id)
                if self.governance_service is not None
                else await self.registry.get_server(server_id)
            )
            if server is None or not server.enabled:
                continue
            binding = MCPBindingResolution(
                server_id=binding.server_id,
                server_key=server.server_key,
                scope_type=binding.scope_type,
                scope_id=binding.scope_id,
                allowed_tool_names=binding.allowed_tool_names,
            )
            filtered_tools = await self._filtered_tools_for_binding(auth, server, binding)
            if not filtered_tools:
                continue
            visible_servers.append(
                VisibleMCPServer(
                    server=server,
                    binding=binding,
                    tool_names=tuple(tool.original_name for tool in filtered_tools),
                )
            )
        visible_servers.sort(key=lambda item: item.server.server_key)
        return visible_servers

    async def _visible_server_by_key(self, auth: UserAPIKeyAuth, server_key: str) -> VisibleMCPServer | None:
        for visible in await self.list_visible_servers(auth):
            if visible.server.server_key == server_key:
                return visible
        return None

    async def _filtered_tools_for_server(self, auth: UserAPIKeyAuth, visible: VisibleMCPServer) -> list[NamespacedTool]:
        all_tools = await self.registry.list_namespaced_tools(visible.server)
        allowed = set(visible.tool_names)
        filtered: list[NamespacedTool] = []
        for tool in all_tools:
            if tool.original_name not in allowed:
                continue
            policy = await self._effective_tool_policy(auth, visible.server.mcp_server_id, tool.original_name)
            if policy is not None and not policy.enabled:
                continue
            filtered.append(
                NamespacedTool(
                    server_key=tool.server_key,
                    original_name=tool.original_name,
                    namespaced_name=tool.namespaced_name,
                    description=tool.description,
                    input_schema=dict(tool.input_schema or {}),
                    scope_type=visible.binding.scope_type,
                    scope_id=visible.binding.scope_id,
                )
            )
        return filtered

    async def resolve_tool_scope(self, auth: UserAPIKeyAuth, *, namespaced_tool_name: str) -> MCPResolvedToolScope | None:
        try:
            server_key, tool_name = parse_namespaced_tool_name(namespaced_tool_name)
        except MCPToolNotFoundError:
            return None
        visible = await self._visible_server_by_key(auth, server_key)
        if visible is None or tool_name not in set(visible.tool_names):
            return None
        return MCPResolvedToolScope(
            server_key=server_key,
            tool_name=tool_name,
            scope_type=visible.binding.scope_type,
            scope_id=visible.binding.scope_id,
        )

    async def tool_requires_manual_approval(
        self,
        auth: UserAPIKeyAuth,
        *,
        server_key: str,
        tool_name: str,
    ) -> bool:
        visible = await self._visible_server_by_key(auth, server_key)
        if visible is None or tool_name not in set(visible.tool_names):
            return False
        policy = await self._effective_tool_policy(auth, visible.server.mcp_server_id, tool_name)
        return bool(policy is not None and policy.require_approval == "manual" and policy.enabled)

    async def _filtered_tools_for_binding(
        self,
        auth: UserAPIKeyAuth,
        server: MCPServerRecord,
        binding: MCPBindingResolution,
    ) -> list[NamespacedTool]:
        tools = await self.registry.list_namespaced_tools(server)
        if binding.allowed_tool_names is None:
            candidate_tools = list(tools)
        else:
            allowed = set(binding.allowed_tool_names)
            candidate_tools = [tool for tool in tools if tool.original_name in allowed]

        filtered: list[NamespacedTool] = []
        for tool in candidate_tools:
            policy = await self._effective_tool_policy(auth, server.mcp_server_id, tool.original_name)
            if policy is not None and not policy.enabled:
                continue
            filtered.append(tool)
        return filtered

    async def _effective_tool_policy(
        self,
        auth: UserAPIKeyAuth,
        server_id: str,
        tool_name: str,
    ) -> MCPToolPolicyRecord | None:
        scope_context = resolve_runtime_scope_context(auth)
        if scope_context.is_master_key:
            return None
        policies = (
            self.governance_service.list_effective_tool_policies(
                scopes=scope_context.scope_chain,
                server_id=server_id,
            )
            if self.governance_service is not None
            else await self.registry.list_effective_tool_policies(
                scopes=list(scope_context.scope_chain),
                server_id=server_id,
            )
        )
        candidates = [policy for policy in policies if policy.tool_name == tool_name]
        if not candidates:
            return None
        return self._select_policy(candidates, scope_order=scope_context.scope_chain)

    @staticmethod
    def _select_binding(
        bindings: list[MCPServerBindingRecord],
        *,
        scope_order: tuple[tuple[str, str], ...],
    ) -> MCPBindingResolution:
        if not bindings:
            raise MCPAccessDeniedError("No MCP binding available")
        selected = _select_record_for_scope_order(bindings, scope_order=scope_order)
        allowed_tools = tuple(selected.tool_allowlist or []) or None
        return MCPBindingResolution(
            server_id=selected.mcp_server_id,
            server_key="",
            scope_type=selected.scope_type,
            scope_id=selected.scope_id,
            allowed_tool_names=allowed_tools,
        )

    @staticmethod
    def _select_policy(
        policies: list[MCPToolPolicyRecord],
        *,
        scope_order: tuple[tuple[str, str], ...],
    ) -> MCPToolPolicyRecord:
        return _select_record_for_scope_order(policies, scope_order=scope_order)


def _select_record_for_scope_order(
    records: list[_ScopeRecordT],
    *,
    scope_order: tuple[tuple[str, str], ...],
) -> _ScopeRecordT:
    if not records:
        raise MCPAccessDeniedError("No MCP scope record available")
    scoped_records = {(record.scope_type, record.scope_id): record for record in records}
    for scope in scope_order:
        match = scoped_records.get(scope)
        if match is not None:
            return match
    return records[0]
