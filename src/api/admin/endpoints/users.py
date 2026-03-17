from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from src.api.admin.endpoints.common import (
    AuthScope,
    db_or_503,
    emit_admin_mutation_audit,
    get_auth_scope,
    validate_runtime_user_scope,
)
from src.auth.roles import Permission
from src.audit.actions import AuditAction
from src.services.asset_visibility_preview import build_asset_visibility_preview
from src.services.scoped_asset_access import apply_scope_asset_access, build_scope_asset_access

router = APIRouter(tags=["Admin Users"])


async def _require_runtime_user_access(
    request: Request,
    scope: AuthScope,
    db: Any,
    user_id: str,
    *,
    write: bool = False,
) -> dict[str, Any]:
    row = await validate_runtime_user_scope(db, user_id)
    if scope.is_platform_admin:
        return row

    organization_id = str(row.get("organization_id") or "").strip()
    team_id = str(row.get("team_id") or "").strip()
    if organization_id and organization_id in scope.org_ids:
        return row
    if team_id and team_id in scope.team_ids:
        return row

    required = Permission.USER_UPDATE if write else Permission.USER_READ
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Insufficient permissions for {required}")


@router.get("/ui/api/users/{user_id}/asset-visibility")
async def get_user_asset_visibility(
    request: Request,
    user_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.USER_READ)
    db = db_or_503(request)
    row = await _require_runtime_user_access(request, scope, db, user_id)
    organization_id = str(row.get("organization_id") or "").strip()
    team_id = str(row.get("team_id") or "").strip() or None
    if not organization_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User organization is not configured")
    return await build_asset_visibility_preview(
        request,
        organization_id=organization_id,
        team_id=team_id,
        user_id=user_id,
    )


@router.get("/ui/api/users/{user_id}/asset-access")
async def get_user_asset_access(
    request: Request,
    user_id: str,
    include_targets: bool = Query(default=True),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.USER_READ)
    db = db_or_503(request)
    row = await _require_runtime_user_access(request, scope, db, user_id)
    organization_id = str(row.get("organization_id") or "").strip()
    team_id = str(row.get("team_id") or "").strip() or None
    if not organization_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User organization is not configured")
    return await build_scope_asset_access(
        request,
        scope_type="user",
        scope_id=user_id,
        organization_id=organization_id,
        team_id=team_id,
        user_id=user_id,
        include_targets=include_targets,
    )


@router.put("/ui/api/users/{user_id}/asset-access")
async def update_user_asset_access(
    request: Request,
    user_id: str,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.USER_UPDATE)
    db = db_or_503(request)
    row = await _require_runtime_user_access(request, scope, db, user_id, write=True)
    organization_id = str(row.get("organization_id") or "").strip()
    team_id = str(row.get("team_id") or "").strip() or None
    if not organization_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User organization is not configured")
    response = await apply_scope_asset_access(
        request,
        scope_type="user",
        scope_id=user_id,
        organization_id=organization_id,
        team_id=team_id,
        user_id=user_id,
        mode=payload.get("mode"),
        selected_callable_keys=payload.get("selected_callable_keys", []),
        select_all_selectable=bool(payload.get("select_all_selectable", False)),
    )
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_USER_UPDATE,
        scope=scope,
        resource_type="user_asset_access",
        resource_id=user_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


__all__ = ["router"]
