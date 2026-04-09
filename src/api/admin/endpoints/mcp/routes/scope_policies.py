"""Admin MCP scope-policy routes."""
from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from src.api.admin.endpoints.common import emit_admin_mutation_audit, get_auth_scope
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.db.mcp_scope_policies import MCPScopePolicyRepository
from src.middleware.admin import require_admin_permission

from src.api.admin.endpoints.mcp.dependencies import (
    _db_or_503,
    _reload_runtime_governance,
    _scope_policy_repository_or_503,
)
from src.api.admin.endpoints.mcp.scope_visibility import _validate_scoped_scope_target_write
from src.api.admin.endpoints.mcp.serializers import _serialize_scope_policy
from src.api.admin.endpoints.mcp.sql_visibility import _scoped_entity_visibility_clause
from src.api.admin.endpoints.mcp.validators import (
    _normalize_metadata,
    _normalize_scope_id,
    _validate_mcp_scope_policy_mode,
    _validate_mcp_scope_policy_scope_type,
)

router = APIRouter(tags=["Admin MCP"])


@router.get("/ui/api/mcp-scope-policies", dependencies=[Depends(require_admin_permission(Permission.KEY_READ))])
async def list_mcp_scope_policies(
    request: Request,
    scope_type: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    repository = _scope_policy_repository_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
    normalized_scope_type = _validate_mcp_scope_policy_scope_type(scope_type) if scope_type is not None else None
    if scope.is_platform_admin:
        policies, total = await repository.list_policies(
            scope_type=normalized_scope_type,
            scope_id=scope_id,
            limit=limit,
            offset=offset,
        )
        return {
            "data": [_serialize_scope_policy(policy) for policy in policies],
            "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
        }

    if not scope.org_ids and not scope.team_ids:
        return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}

    db = _db_or_503(request)
    clauses: list[str] = []
    params: list[Any] = []
    if normalized_scope_type:
        params.append(normalized_scope_type)
        clauses.append(f"p.scope_type = ${len(params)}")
    if scope_id:
        params.append(scope_id)
        clauses.append(f"p.scope_id = ${len(params)}")
    clauses.append(f"({_scoped_entity_visibility_clause('p', scope, params)})")
    where_sql = f" WHERE {' AND '.join(clauses)}"

    count_rows = await db.query_raw(
        f"SELECT COUNT(*)::int AS total FROM deltallm_mcpscopepolicy p {where_sql}",
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)

    page_params = [*params, limit, offset]
    rows = await db.query_raw(
        f"""
        SELECT
            p.mcp_scope_policy_id,
            p.scope_type,
            p.scope_id,
            p.mode,
            p.metadata,
            p.created_at,
            p.updated_at
        FROM deltallm_mcpscopepolicy p
        {where_sql}
        ORDER BY p.created_at DESC, p.scope_type ASC, p.scope_id ASC
        LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
        """,
        *page_params,
    )
    policies = [MCPScopePolicyRepository._to_policy_record(row) for row in rows]
    return {
        "data": [_serialize_scope_policy(policy) for policy in policies],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.post("/ui/api/mcp-scope-policies", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def upsert_mcp_scope_policy(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _scope_policy_repository_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_UPDATE)
    scope_type = _validate_mcp_scope_policy_scope_type(payload.get("scope_type"))
    scope_id = _normalize_scope_id(payload.get("scope_id"))
    await _validate_scoped_scope_target_write(request, scope=scope, scope_type=scope_type, scope_id=scope_id)

    policy = await repository.upsert_policy(
        scope_type=scope_type,
        scope_id=scope_id,
        mode=_validate_mcp_scope_policy_mode(payload.get("mode")),
        metadata=_normalize_metadata(payload.get("metadata")),
    )
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP scope policy not found")
    await _reload_runtime_governance(request)
    response = _serialize_scope_policy(policy)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_SCOPE_POLICY_UPSERT,
        scope=scope,
        resource_type="mcp_scope_policy",
        resource_id=policy.mcp_scope_policy_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.delete("/ui/api/mcp-scope-policies/{policy_id}", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def delete_mcp_scope_policy(
    request: Request,
    policy_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _scope_policy_repository_or_503(request)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_UPDATE)
    policy = await repository.get_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP scope policy not found")
    await _validate_scoped_scope_target_write(
        request,
        scope=scope,
        scope_type=policy.scope_type,
        scope_id=policy.scope_id,
    )
    deleted = await repository.delete_policy(policy_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP scope policy not found")
    await _reload_runtime_governance(request)
    response = {"deleted": True, "mcp_scope_policy_id": policy_id}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_SCOPE_POLICY_DELETE,
        scope=scope,
        resource_type="mcp_scope_policy",
        resource_id=policy_id,
        response_payload=response,
    )
    return response


__all__ = ["router"]
