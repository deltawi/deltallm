"""Response serializers for the admin MCP endpoints."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from src.api.admin.endpoints.common import to_json_value
from src.db.mcp import (
    MCPApprovalRequestRecord,
    MCPServerBindingRecord,
    MCPServerRecord,
    MCPToolPolicyRecord,
)
from src.db.mcp_scope_policies import MCPScopePolicyRecord
from src.mcp.capabilities import extract_tool_schemas

from src.api.admin.endpoints.mcp.scope_visibility import MCPServerCapabilities
from src.api.admin.endpoints.mcp.validators import _credentials_present


def _serialize_server(
    server: MCPServerRecord,
    *,
    capabilities: MCPServerCapabilities | None = None,
) -> dict[str, Any]:
    payload = to_json_value(asdict(server))
    payload.pop("auth_config", None)
    payload["tool_count"] = len(extract_tool_schemas(server.capabilities_json or {}))
    payload["auth_credentials_present"] = _credentials_present(server.auth_mode, server.auth_config)
    if capabilities is not None:
        payload["capabilities"] = to_json_value(asdict(capabilities))
    return payload


def _serialize_binding(binding: MCPServerBindingRecord) -> dict[str, Any]:
    return to_json_value(asdict(binding))


def _policy_max_total_execution_time_ms(policy: MCPToolPolicyRecord) -> int | None:
    value = (policy.metadata or {}).get("max_total_mcp_execution_time_ms")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _serialize_policy(policy: MCPToolPolicyRecord) -> dict[str, Any]:
    payload = to_json_value(asdict(policy))
    payload["max_total_execution_time_ms"] = _policy_max_total_execution_time_ms(policy)
    return payload


def _serialize_scope_policy(policy: MCPScopePolicyRecord) -> dict[str, Any]:
    return to_json_value(asdict(policy))


def _serialize_server_summary(server: MCPServerRecord | None, *, server_id: str | None = None) -> dict[str, Any]:
    if server is None:
        return {
            "mcp_server_id": server_id,
            "server_key": None,
            "name": None,
            "owner_scope_type": None,
            "owner_scope_id": None,
        }
    return {
        "mcp_server_id": server.mcp_server_id,
        "server_key": server.server_key,
        "name": server.name,
        "owner_scope_type": server.owner_scope_type,
        "owner_scope_id": server.owner_scope_id,
    }


def _serialize_approval_request(
    record: MCPApprovalRequestRecord,
    *,
    server: MCPServerRecord | None = None,
    can_decide: bool = False,
) -> dict[str, Any]:
    payload = to_json_value(asdict(record))
    payload["server"] = _serialize_server_summary(server, server_id=record.mcp_server_id)
    payload["capabilities"] = {"can_decide": bool(can_decide and record.status == "pending")}
    return payload


__all__ = [
    "_serialize_server",
    "_serialize_binding",
    "_policy_max_total_execution_time_ms",
    "_serialize_policy",
    "_serialize_scope_policy",
    "_serialize_server_summary",
    "_serialize_approval_request",
]
