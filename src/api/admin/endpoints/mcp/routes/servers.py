"""Admin MCP server routes."""
from __future__ import annotations

from dataclasses import asdict
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from src.api.admin.endpoints.common import (
    emit_admin_mutation_audit,
    get_auth_scope,
    to_json_value,
)
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.db.mcp import MCPRepository
from src.mcp.exceptions import MCPError
from src.middleware.admin import require_admin_permission
from src.middleware.platform_auth import get_platform_auth_context

from src.api.admin.endpoints.mcp.dependencies import (
    _db_or_503,
    _health_probe_or_503,
    _registry_or_503,
    _reload_runtime_governance,
    _repository_or_503,
)
from src.api.admin.endpoints.mcp.loaders import (
    _list_scoped_bindings,
    _list_scoped_tool_policies,
    _load_server_or_404,
    _server_visible_to_scope,
)
from src.api.admin.endpoints.mcp.operations import (
    _capability_refresh,
    _filter_server_tools_for_scope,
    _request_timeout_ms,
)
from src.api.admin.endpoints.mcp.scope_visibility import (
    _resolve_server_create_owner_scope,
    _server_mutable_by_scope,
    _server_owned_by_scope,
    _server_view_capabilities,
)
from src.api.admin.endpoints.mcp.serializers import (
    _serialize_binding,
    _serialize_policy,
    _serialize_server,
)
from src.api.admin.endpoints.mcp.sql_visibility import (
    _approval_visibility_clause,
    _audit_scope_visibility_clause,
    _server_visibility_exists_clause,
)
from src.api.admin.endpoints.mcp.validators import (
    _normalize_allowlist,
    _normalize_auth_mode,
    _normalize_metadata,
    _normalize_scope_id,
    _normalize_server_key,
    _normalize_transport,
    _sanitize_auth_payload,
    _validate_auth_config,
    _validate_owner_scope_type,
    _validate_url,
)

router = APIRouter(tags=["Admin MCP"])


@router.get("/ui/api/mcp-servers", dependencies=[Depends(require_admin_permission(Permission.KEY_READ))])
async def list_mcp_servers(
    request: Request,
    search: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    registry = _registry_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
    manage_scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_UPDATE)
    if scope.is_platform_admin:
        servers, total = await registry.list_servers(search=search, enabled=enabled, limit=limit, offset=offset)
        return {
            "data": [
                _serialize_server(
                    server,
                    capabilities=_server_view_capabilities(server, manage_scope=manage_scope, is_visible=True),
                )
                for server in servers
            ],
            "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
        }

    if not scope.org_ids and not scope.team_ids:
        return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}

    db = _db_or_503(request)
    clauses: list[str] = []
    params: list[Any] = []
    if search:
        params.append(f"%{search.strip()}%")
        clauses.append(
            f"(s.server_key ILIKE ${len(params)} OR s.name ILIKE ${len(params)} OR COALESCE(s.description, '') ILIKE ${len(params)})"
        )
    if enabled is not None:
        params.append(enabled)
        clauses.append(f"s.enabled = ${len(params)}")
    clauses.append(_server_visibility_exists_clause("s", scope, params))
    where_sql = f" WHERE {' AND '.join(clauses)}"

    count_rows = await db.query_raw(
        f"SELECT COUNT(*)::int AS total FROM deltallm_mcpserver s {where_sql}",
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)

    page_params = [*params, limit, offset]
    rows = await db.query_raw(
        f"""
        SELECT
            s.mcp_server_id,
            s.server_key,
            s.name,
            s.description,
            s.owner_scope_type,
            s.owner_scope_id,
            s.transport,
            s.base_url,
            s.enabled,
            s.auth_mode,
            s.auth_config,
            s.forwarded_headers_allowlist,
            s.request_timeout_ms,
            s.capabilities_json,
            s.capabilities_etag,
            s.capabilities_fetched_at,
            s.last_health_status,
            s.last_health_error,
            s.last_health_at,
            s.last_health_latency_ms,
            s.metadata,
            s.created_by_account_id,
            s.created_at,
            s.updated_at
        FROM deltallm_mcpserver s
        {where_sql}
        ORDER BY s.created_at DESC, s.server_key ASC
        LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
        """,
        *page_params,
    )
    servers = [MCPRepository._to_server_record(row) for row in rows]
    return {
        "data": [
            _serialize_server(
                server,
                capabilities=_server_view_capabilities(server, manage_scope=manage_scope, is_visible=True),
            )
            for server in servers
        ],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.post("/ui/api/mcp-servers", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def create_mcp_server(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    _registry_or_503(request)  # Health check only
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_UPDATE)

    server_key = _normalize_server_key(payload.get("server_key"))
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")
    auth_mode = _normalize_auth_mode(payload.get("auth_mode"))
    existing = await repository.get_server_by_key(server_key)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An MCP server with this server_key already exists")
    owner_scope_type, owner_scope_id = await _resolve_server_create_owner_scope(request, scope=scope, payload=payload)

    created_by_account_id = getattr(get_platform_auth_context(request), "account_id", None)
    created = await repository.create_server(
        server_key=server_key,
        name=name,
        description=str(payload.get("description")).strip() if payload.get("description") is not None else None,
        owner_scope_type=owner_scope_type,
        owner_scope_id=owner_scope_id,
        transport=_normalize_transport(payload.get("transport")),
        base_url=_validate_url(payload.get("base_url")),
        enabled=bool(payload.get("enabled", True)),
        auth_mode=auth_mode,
        auth_config=_validate_auth_config(auth_mode, payload.get("auth_config")),
        forwarded_headers_allowlist=_normalize_allowlist(payload.get("forwarded_headers_allowlist")),
        request_timeout_ms=_request_timeout_ms(payload, default=30000),
        metadata=_normalize_metadata(payload.get("metadata")),
        created_by_account_id=created_by_account_id,
    )
    await _reload_runtime_governance(request, invalidate_registry=False)
    response = _serialize_server(
        created,
        capabilities=_server_view_capabilities(created, manage_scope=scope, is_visible=True),
    )
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_SERVER_CREATE,
        scope=scope,
        resource_type="mcp_server",
        resource_id=created.mcp_server_id,
        request_payload=_sanitize_auth_payload(payload, auth_mode=auth_mode),
        response_payload=response,
    )
    return response


@router.get("/ui/api/mcp-servers/{server_id}", dependencies=[Depends(require_admin_permission(Permission.KEY_READ))])
async def get_mcp_server(
    request: Request,
    server_id: str,
    include_disabled: bool = Query(default=False),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    registry = _registry_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
    manage_scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_UPDATE)
    server = await _load_server_or_404(request, server_id)
    is_visible = await _server_visible_to_scope(request, scope, server_id)
    if not is_visible:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    enabled_only = not (scope.is_platform_admin and include_disabled)
    bindings, _ = await _list_scoped_bindings(
        request,
        scope=scope,
        server_id=server_id,
        enabled_only=enabled_only,
        limit=200,
        offset=0,
    )
    policies, _ = await _list_scoped_tool_policies(
        request,
        scope=scope,
        server_id=server_id,
        enabled_only=enabled_only,
        limit=200,
        offset=0,
    )
    tools = _filter_server_tools_for_scope(
        await registry.list_namespaced_tools(server),
        bindings=bindings,
        policies=policies,
        include_all=_server_owned_by_scope(server, scope),
    )
    return {
        "server": _serialize_server(
            server,
            capabilities=_server_view_capabilities(server, manage_scope=manage_scope, is_visible=is_visible),
        ),
        "tools": [to_json_value(asdict(item)) for item in tools],
        "bindings": [_serialize_binding(binding) for binding in bindings],
        "tool_policies": [_serialize_policy(policy) for policy in policies],
    }


@router.get("/ui/api/mcp-servers/{server_id}/operations", dependencies=[Depends(require_admin_permission(Permission.KEY_READ))])
async def get_mcp_server_operations(
    request: Request,
    server_id: str,
    window_hours: int = Query(default=24, ge=1, le=24 * 30),
    top_tools_limit: int = Query(default=5, ge=1, le=20),
    failures_limit: int = Query(default=5, ge=1, le=20),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    db = _db_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
    server = await _load_server_or_404(request, server_id)
    if not await _server_visible_to_scope(request, scope, server_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    params: list[Any] = [AuditAction.MCP_TOOL_CALL.value, server.server_key, window_hours]
    scope_sql = ""
    approval_scope_sql = ""
    approval_params: list[Any] = [server.mcp_server_id, window_hours]
    if not scope.is_platform_admin:
        scope_sql = f" AND ({_audit_scope_visibility_clause(scope, params)})"
        approval_scope_sql = f" AND ({_approval_visibility_clause(scope, approval_params)})"

    summary_rows = await db.query_raw(
        f"""
        SELECT
            COUNT(*)::int AS total_calls,
            COUNT(*) FILTER (WHERE status = 'error')::int AS failed_calls,
            COALESCE(AVG(latency_ms), 0)::float8 AS avg_latency_ms
        FROM deltallm_auditevent
        WHERE action = $1
          AND resource_type = 'mcp_tool'
          AND metadata->>'server_key' = $2
          AND occurred_at >= NOW() - ($3::int * INTERVAL '1 hour')
          {scope_sql}
        """,
        *params,
    )
    summary_row = summary_rows[0] if summary_rows else {}
    total_calls = int(summary_row.get("total_calls") or 0)
    failed_calls = int(summary_row.get("failed_calls") or 0)
    avg_latency_ms = float(summary_row.get("avg_latency_ms") or 0.0)

    tool_params = [*params, top_tools_limit]
    tool_rows = await db.query_raw(
        f"""
        SELECT
            resource_id AS tool_name,
            COUNT(*)::int AS total_calls,
            COUNT(*) FILTER (WHERE status = 'error')::int AS failed_calls,
            COALESCE(AVG(latency_ms), 0)::float8 AS avg_latency_ms
        FROM deltallm_auditevent
        WHERE action = $1
          AND resource_type = 'mcp_tool'
          AND metadata->>'server_key' = $2
          AND occurred_at >= NOW() - ($3::int * INTERVAL '1 hour')
          {scope_sql}
        GROUP BY resource_id
        ORDER BY total_calls DESC, resource_id ASC
        LIMIT ${len(tool_params)}
        """,
        *tool_params,
    )

    failure_params = [*params, failures_limit]
    failure_rows = await db.query_raw(
        f"""
        SELECT
            event_id,
            occurred_at,
            resource_id AS tool_name,
            error_type,
            error_code,
            latency_ms,
            request_id
        FROM deltallm_auditevent
        WHERE action = $1
          AND resource_type = 'mcp_tool'
          AND metadata->>'server_key' = $2
          AND status = 'error'
          AND occurred_at >= NOW() - ($3::int * INTERVAL '1 hour')
          {scope_sql}
        ORDER BY occurred_at DESC
        LIMIT ${len(failure_params)}
        """,
        *failure_params,
    )

    approval_rows = await db.query_raw(
        f"""
        SELECT
            COUNT(*)::int AS total_requests,
            COUNT(*) FILTER (WHERE status = 'pending')::int AS pending_requests,
            COUNT(*) FILTER (WHERE status = 'approved')::int AS approved_requests,
            COUNT(*) FILTER (WHERE status = 'rejected')::int AS rejected_requests
        FROM deltallm_mcpapprovalrequest
        WHERE mcp_server_id = $1
          AND created_at >= NOW() - ($2::int * INTERVAL '1 hour')
          {approval_scope_sql}
        """,
        *approval_params,
    )
    approval_row = approval_rows[0] if approval_rows else {}

    return {
        "window_hours": window_hours,
        "summary": {
            "total_calls": total_calls,
            "failed_calls": failed_calls,
            "success_calls": max(total_calls - failed_calls, 0),
            "failure_rate": (failed_calls / total_calls) if total_calls else 0.0,
            "avg_latency_ms": avg_latency_ms,
            "approval_requests": int(approval_row.get("total_requests") or 0),
            "pending_approvals": int(approval_row.get("pending_requests") or 0),
            "approved_approvals": int(approval_row.get("approved_requests") or 0),
            "rejected_approvals": int(approval_row.get("rejected_requests") or 0),
        },
        "top_tools": [to_json_value(dict(row)) for row in tool_rows],
        "recent_failures": [to_json_value(dict(row)) for row in failure_rows],
    }


@router.patch("/ui/api/mcp-servers/{server_id}", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def update_mcp_server(
    request: Request,
    server_id: str,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    registry = _registry_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_UPDATE)

    existing = await _load_server_or_404(request, server_id)
    if not _server_mutable_by_scope(existing, scope):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    if "owner_scope_type" in payload or "owner_scope_id" in payload:
        requested_owner_scope_type = _validate_owner_scope_type(payload.get("owner_scope_type", existing.owner_scope_type))
        requested_owner_scope_id = (
            _normalize_scope_id(payload.get("owner_scope_id"), field_name="owner_scope_id")
            if payload.get("owner_scope_id") is not None
            else existing.owner_scope_id
        )
        if requested_owner_scope_type != existing.owner_scope_type or requested_owner_scope_id != existing.owner_scope_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MCP server ownership cannot be changed")
    auth_mode = _normalize_auth_mode(payload.get("auth_mode", existing.auth_mode))
    updated = await repository.update_server(
        server_id,
        name=str(payload.get("name", existing.name) or "").strip() or existing.name,
        description=str(payload.get("description")).strip() if payload.get("description") is not None else existing.description,
        transport=_normalize_transport(payload.get("transport", existing.transport)),
        base_url=_validate_url(payload.get("base_url", existing.base_url)),
        enabled=bool(payload.get("enabled", existing.enabled)),
        auth_mode=auth_mode,
        auth_config=_validate_auth_config(auth_mode, payload.get("auth_config", existing.auth_config or {})),
        forwarded_headers_allowlist=_normalize_allowlist(
            payload.get("forwarded_headers_allowlist", existing.forwarded_headers_allowlist or [])
        ),
        request_timeout_ms=_request_timeout_ms(payload, default=existing.request_timeout_ms),
        metadata=_normalize_metadata(payload.get("metadata", existing.metadata)) or None,
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    await registry.invalidate_server(updated.server_key)
    await _reload_runtime_governance(request, invalidate_registry=False)
    response = _serialize_server(
        updated,
        capabilities=_server_view_capabilities(updated, manage_scope=scope, is_visible=True),
    )
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_SERVER_UPDATE,
        scope=scope,
        resource_type="mcp_server",
        resource_id=updated.mcp_server_id,
        request_payload=_sanitize_auth_payload(payload, auth_mode=auth_mode),
        response_payload=response,
        before=_serialize_server(existing),
        after=response,
    )
    return response


@router.delete("/ui/api/mcp-servers/{server_id}", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def delete_mcp_server(
    request: Request,
    server_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    registry = _registry_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_UPDATE)

    server = await _load_server_or_404(request, server_id)
    if not _server_mutable_by_scope(server, scope):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    deleted = await repository.delete_server(server_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    await registry.invalidate_server(server.server_key)
    await _reload_runtime_governance(request)
    response = {"deleted": True, "mcp_server_id": server_id}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_SERVER_DELETE,
        scope=scope,
        resource_type="mcp_server",
        resource_id=server_id,
        response_payload=response,
        before=_serialize_server(server),
    )
    return response


@router.post("/ui/api/mcp-servers/{server_id}/refresh-capabilities", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def refresh_mcp_server_capabilities(
    request: Request,
    server_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_UPDATE)
    server = await _load_server_or_404(request, server_id)
    is_visible = await _server_visible_to_scope(request, scope, server_id)
    capabilities = _server_view_capabilities(server, manage_scope=scope, is_visible=is_visible)
    if not capabilities.can_operate:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    try:
        updated, tools = await _capability_refresh(request, server)
    except MCPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    response = {
        "server": _serialize_server(
            updated,
            capabilities=_server_view_capabilities(updated, manage_scope=scope, is_visible=True),
        ),
        "tools": tools,
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_SERVER_REFRESH_CAPABILITIES,
        scope=scope,
        resource_type="mcp_server",
        resource_id=server_id,
        response_payload=response,
    )
    return response


@router.post("/ui/api/mcp-servers/{server_id}/health-check", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def health_check_mcp_server(
    request: Request,
    server_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_UPDATE)
    server = await _load_server_or_404(request, server_id)
    is_visible = await _server_visible_to_scope(request, scope, server_id)
    capabilities = _server_view_capabilities(server, manage_scope=scope, is_visible=is_visible)
    if not capabilities.can_operate:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    probe = _health_probe_or_503(request)
    try:
        result = await probe.check_server(server)
    except MCPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    refreshed = await _load_server_or_404(request, server_id)
    response = {
        "server": _serialize_server(
            refreshed,
            capabilities=_server_view_capabilities(refreshed, manage_scope=scope, is_visible=True),
        ),
        "health": {"status": result.status, "latency_ms": result.latency_ms, "error": result.error},
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_SERVER_HEALTH_CHECK,
        scope=scope,
        resource_type="mcp_server",
        resource_id=server_id,
        response_payload=response,
    )
    return response


__all__ = ["router"]
