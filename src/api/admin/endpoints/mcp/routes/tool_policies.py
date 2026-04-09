"""Admin MCP tool-policy routes."""
from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from src.api.admin.endpoints.common import emit_admin_mutation_audit, get_auth_scope, optional_int
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
from src.api.admin.endpoints.mcp.serializers import _serialize_policy
from src.api.admin.endpoints.mcp.sql_visibility import _scoped_entity_visibility_clause
from src.api.admin.endpoints.mcp.validators import (
    _normalize_scope_id,
    _normalize_tool_name,
    _normalize_tool_policy_metadata,
    _validate_require_approval,
    _validate_scope_type,
)

router = APIRouter(tags=["Admin MCP"])


@router.get("/ui/api/mcp-tool-policies", dependencies=[Depends(require_admin_permission(Permission.KEY_READ))])
async def list_mcp_tool_policies(
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
        policies, total = await repository.list_tool_policies(
            server_id=server_id,
            scope_type=normalized_scope_type,
            scope_id=scope_id,
            enabled=None if include_disabled else True,
            limit=limit,
            offset=offset,
        )
        return {
            "data": [_serialize_policy(policy) for policy in policies],
            "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
        }

    if not scope.org_ids and not scope.team_ids:
        return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}

    db = _db_or_503(request)
    clauses: list[str] = []
    params: list[Any] = []
    if server_id:
        params.append(server_id)
        clauses.append(f"p.mcp_server_id = ${len(params)}")
    if normalized_scope_type:
        params.append(normalized_scope_type)
        clauses.append(f"p.scope_type = ${len(params)}")
    if scope_id:
        params.append(scope_id)
        clauses.append(f"p.scope_id = ${len(params)}")
    clauses.append("p.enabled = true")
    clauses.append(f"({_scoped_entity_visibility_clause('p', scope, params)})")
    where_sql = f" WHERE {' AND '.join(clauses)}"

    count_rows = await db.query_raw(
        f"SELECT COUNT(*)::int AS total FROM deltallm_mcptoolpolicy p {where_sql}",
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)

    page_params = [*params, limit, offset]
    rows = await db.query_raw(
        f"""
        SELECT
            p.mcp_tool_policy_id,
            p.mcp_server_id,
            p.tool_name,
            p.scope_type,
            p.scope_id,
            p.enabled,
            p.require_approval,
            p.max_rpm,
            p.max_concurrency,
            p.result_cache_ttl_seconds,
            p.metadata,
            p.created_at,
            p.updated_at
        FROM deltallm_mcptoolpolicy p
        {where_sql}
        ORDER BY p.created_at DESC, p.tool_name ASC, p.scope_type ASC, p.scope_id ASC
        LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
        """,
        *page_params,
    )
    policies = [MCPRepository._to_tool_policy_record(row) for row in rows]
    return {
        "data": [_serialize_policy(policy) for policy in policies],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.post("/ui/api/mcp-tool-policies", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def upsert_mcp_tool_policy(
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
    policy = await repository.upsert_tool_policy(
        server_id=server.mcp_server_id,
        tool_name=_normalize_tool_name(payload.get("tool_name")),
        scope_type=scope_type,
        scope_id=scope_id,
        enabled=bool(payload.get("enabled", True)),
        require_approval=_validate_require_approval(payload.get("require_approval")),
        max_rpm=optional_int(payload.get("max_rpm"), "max_rpm"),
        max_concurrency=optional_int(payload.get("max_concurrency"), "max_concurrency"),
        result_cache_ttl_seconds=optional_int(payload.get("result_cache_ttl_seconds"), "result_cache_ttl_seconds"),
        metadata=_normalize_tool_policy_metadata(payload),
    )
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    await _reload_runtime_governance(request)
    response = _serialize_policy(policy)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_TOOL_POLICY_UPSERT,
        scope=scope,
        resource_type="mcp_tool_policy",
        resource_id=policy.mcp_tool_policy_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.delete("/ui/api/mcp-tool-policies/{policy_id}", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def delete_mcp_tool_policy(
    request: Request,
    policy_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_UPDATE)
    policy = await repository.get_tool_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP tool policy not found")
    server = await _load_server_or_404(request, policy.mcp_server_id)
    if not _server_scope_config_writable_by_scope(server, scope):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    await _validate_scoped_server_target_write(
        request,
        scope=scope,
        server=server,
        scope_type=policy.scope_type,
        scope_id=policy.scope_id,
    )
    deleted = await repository.delete_tool_policy(policy_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP tool policy not found")
    await _reload_runtime_governance(request)
    response = {"deleted": True, "mcp_tool_policy_id": policy_id}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_TOOL_POLICY_DELETE,
        scope=scope,
        resource_type="mcp_tool_policy",
        resource_id=policy_id,
        response_payload=response,
    )
    return response


__all__ = ["router"]
