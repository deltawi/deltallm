"""Admin MCP binding routes."""
from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from src.api.admin.endpoints.common import emit_admin_mutation_audit, get_auth_scope
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.db.mcp import MCPRepository
from src.middleware.admin import require_admin_permission

from src.api.admin.endpoints.mcp.dependencies import (
    _db_or_503,
    _reload_runtime_governance,
    _repository_or_503,
)
from src.api.admin.endpoints.mcp.loaders import _load_server_or_404
from src.api.admin.endpoints.mcp.scope_visibility import (
    _server_scope_config_writable_by_scope,
    _validate_scoped_server_target_write,
)
from src.api.admin.endpoints.mcp.serializers import _serialize_binding
from src.api.admin.endpoints.mcp.sql_visibility import _scoped_entity_visibility_clause
from src.api.admin.endpoints.mcp.validators import (
    _normalize_allowlist,
    _normalize_metadata,
    _normalize_scope_id,
    _validate_scope_type,
)

router = APIRouter(tags=["Admin MCP"])


@router.get("/ui/api/mcp-bindings", dependencies=[Depends(require_admin_permission(Permission.KEY_READ))])
async def list_mcp_bindings(
    request: Request,
    server_id: str | None = Query(default=None),
    scope_type: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    include_disabled: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    repository = _repository_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
    normalized_scope_type = _validate_scope_type(scope_type) if scope_type is not None else None
    if scope.is_platform_admin:
        bindings, total = await repository.list_bindings(
            server_id=server_id,
            scope_type=normalized_scope_type,
            scope_id=scope_id,
            enabled=None if include_disabled else True,
            limit=limit,
            offset=offset,
        )
        return {
            "data": [_serialize_binding(binding) for binding in bindings],
            "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
        }

    if not scope.org_ids and not scope.team_ids:
        return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}

    db = _db_or_503(request)
    clauses: list[str] = []
    params: list[Any] = []
    if server_id:
        params.append(server_id)
        clauses.append(f"b.mcp_server_id = ${len(params)}")
    if normalized_scope_type:
        params.append(normalized_scope_type)
        clauses.append(f"b.scope_type = ${len(params)}")
    if scope_id:
        params.append(scope_id)
        clauses.append(f"b.scope_id = ${len(params)}")
    clauses.append("b.enabled = true")
    clauses.append(f"({_scoped_entity_visibility_clause('b', scope, params)})")
    where_sql = f" WHERE {' AND '.join(clauses)}"

    count_rows = await db.query_raw(
        f"SELECT COUNT(*)::int AS total FROM deltallm_mcpbinding b {where_sql}",
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)

    page_params = [*params, limit, offset]
    rows = await db.query_raw(
        f"""
        SELECT
            b.mcp_binding_id,
            b.mcp_server_id,
            b.scope_type,
            b.scope_id,
            b.enabled,
            b.tool_allowlist,
            b.metadata,
            b.created_at,
            b.updated_at
        FROM deltallm_mcpbinding b
        {where_sql}
        ORDER BY b.created_at DESC, b.scope_type ASC, b.scope_id ASC
        LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
        """,
        *page_params,
    )
    bindings = [MCPRepository._to_binding_record(row) for row in rows]
    return {
        "data": [_serialize_binding(binding) for binding in bindings],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.post("/ui/api/mcp-bindings", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def upsert_mcp_binding(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_UPDATE)
    server = await _load_server_or_404(request, _normalize_scope_id(payload.get("server_id"), field_name="server_id"))
    scope_type = _validate_scope_type(payload.get("scope_type"))
    scope_id = _normalize_scope_id(payload.get("scope_id"))
    if not _server_scope_config_writable_by_scope(server, scope):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    await _validate_scoped_server_target_write(request, scope=scope, server=server, scope_type=scope_type, scope_id=scope_id)
    binding = await repository.upsert_binding(
        server_id=server.mcp_server_id,
        scope_type=scope_type,
        scope_id=scope_id,
        enabled=bool(payload.get("enabled", True)),
        tool_allowlist=_normalize_allowlist(payload.get("tool_allowlist")),
        metadata=_normalize_metadata(payload.get("metadata")),
    )
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    await _reload_runtime_governance(request)
    response = _serialize_binding(binding)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_BINDING_UPSERT,
        scope=scope,
        resource_type="mcp_binding",
        resource_id=binding.mcp_binding_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.delete("/ui/api/mcp-bindings/{binding_id}", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def delete_mcp_binding(
    request: Request,
    binding_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_UPDATE)
    binding = await repository.get_binding(binding_id)
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP binding not found")
    server = await _load_server_or_404(request, binding.mcp_server_id)
    if not _server_scope_config_writable_by_scope(server, scope):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    await _validate_scoped_server_target_write(
        request,
        scope=scope,
        server=server,
        scope_type=binding.scope_type,
        scope_id=binding.scope_id,
    )
    deleted = await repository.delete_binding(binding_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP binding not found")
    await _reload_runtime_governance(request)
    response = {"deleted": True, "mcp_binding_id": binding_id}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_BINDING_DELETE,
        scope=scope,
        resource_type="mcp_binding",
        resource_id=binding_id,
        response_payload=response,
    )
    return response


__all__ = ["router"]
