"""Operational helpers (capability refresh, tool filtering, timeout parsing)."""
from __future__ import annotations

from dataclasses import asdict
from time import perf_counter
from typing import Any

from fastapi import HTTPException, Request, status

from src.api.admin.endpoints.common import optional_int, to_json_value
from src.db.mcp import MCPServerBindingRecord, MCPServerRecord, MCPToolPolicyRecord
from src.mcp.metrics import record_mcp_capability_refresh
from src.mcp.registry import server_record_to_config

from src.api.admin.endpoints.mcp.constants import _SCOPE_SPECIFICITY
from src.api.admin.endpoints.mcp.dependencies import _registry_or_503, _transport_or_503


def _filter_server_tools_for_scope(
    tools: list[Any],
    *,
    bindings: list[MCPServerBindingRecord],
    policies: list[MCPToolPolicyRecord],
    include_all: bool,
) -> list[Any]:
    if include_all:
        return tools
    if not bindings:
        return []

    allowed_names: set[str] = set()
    all_allowed = False
    for binding in bindings:
        if not binding.enabled:
            continue
        allowlist = tuple(binding.tool_allowlist or [])
        if not allowlist:
            all_allowed = True
            break
        allowed_names.update(str(name) for name in allowlist)

    filtered = [tool for tool in tools if all_allowed or str(getattr(tool, "original_name", "")) in allowed_names]
    precedence = {scope_type: index for index, scope_type in enumerate(_SCOPE_SPECIFICITY)}
    effective_policy_by_tool: dict[str, MCPToolPolicyRecord] = {}
    for policy in sorted(policies, key=lambda item: precedence.get(item.scope_type, 999)):
        effective_policy_by_tool.setdefault(policy.tool_name, policy)
    visible_tools: list[Any] = []
    for tool in filtered:
        policy = effective_policy_by_tool.get(str(getattr(tool, "original_name", "")))
        if policy is not None and not policy.enabled:
            continue
        visible_tools.append(tool)
    return visible_tools


async def _capability_refresh(
    request: Request,
    server: MCPServerRecord,
) -> tuple[MCPServerRecord, list[dict[str, Any]]]:
    started = perf_counter()
    registry = _registry_or_503(request)
    transport = _transport_or_503(request)
    config = server_record_to_config(server)
    try:
        await transport.initialize(config)
        tools = await transport.list_tools(config)
    except Exception:
        record_mcp_capability_refresh(
            server_key=server.server_key,
            success=False,
            latency_ms=int((perf_counter() - started) * 1000),
        )
        raise
    capabilities = {
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
                "annotations": tool.annotations,
            }
            for tool in tools
        ]
    }
    updated = await registry.store_server_capabilities(server.mcp_server_id, capabilities=capabilities)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    record_mcp_capability_refresh(
        server_key=server.server_key,
        success=True,
        latency_ms=int((perf_counter() - started) * 1000),
    )
    namespaced = await registry.list_namespaced_tools(updated)
    return updated, [to_json_value(asdict(item)) for item in namespaced]


def _request_timeout_ms(payload: dict[str, Any], *, default: int) -> int:
    timeout_ms = optional_int(payload.get("request_timeout_ms"), "request_timeout_ms")
    value = timeout_ms if timeout_ms is not None else default
    if value <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="request_timeout_ms must be greater than 0")
    return value


__all__ = [
    "_filter_server_tools_for_scope",
    "_capability_refresh",
    "_request_timeout_ms",
]
