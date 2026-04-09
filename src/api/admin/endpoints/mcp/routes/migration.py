"""Admin MCP migration routes."""
from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request

from src.api.admin.endpoints.common import emit_admin_mutation_audit, get_auth_scope
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.middleware.admin import require_admin_permission
from src.services.mcp_migration import (
    apply_mcp_migration_backfill,
    build_mcp_migration_report,
)

from src.api.admin.endpoints.mcp.dependencies import (
    _db_or_503,
    _reload_runtime_governance,
    _repository_or_503,
    _scope_policy_repository_or_503,
)
from src.api.admin.endpoints.mcp.validators import _validate_mcp_migration_rollout_states

router = APIRouter(tags=["Admin MCP"])


@router.get("/ui/api/mcp-migration/report", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def get_mcp_migration_report(
    request: Request,
    organization_id: str | None = Query(default=None),
    rollout_state: list[str] | None = Query(default=None),
) -> dict[str, Any]:
    return await build_mcp_migration_report(
        db=_db_or_503(request),
        repository=_repository_or_503(request),
        policy_repository=getattr(request.app.state, "mcp_scope_policy_repository", None),
        organization_id=str(organization_id).strip() if organization_id is not None else None,
        rollout_states=_validate_mcp_migration_rollout_states(rollout_state),
    )


@router.post("/ui/api/mcp-migration/backfill", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def backfill_mcp_migration(
    request: Request,
    payload: dict[str, Any] | None = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.PLATFORM_ADMIN)
    body = payload or {}
    response = await apply_mcp_migration_backfill(
        db=_db_or_503(request),
        repository=_repository_or_503(request),
        policy_repository=_scope_policy_repository_or_503(request),
        organization_id=str(body.get("organization_id")).strip() if body.get("organization_id") is not None else None,
        rollout_states=_validate_mcp_migration_rollout_states(body.get("rollout_states")),
    )
    await _reload_runtime_governance(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_MIGRATION_BACKFILL,
        scope=scope,
        resource_type="mcp_migration",
        resource_id=str(body.get("organization_id") or "all"),
        request_payload=body,
        response_payload=response,
    )
    return response


__all__ = ["router"]
