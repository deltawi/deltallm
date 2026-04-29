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


def _optional_int(val: Any, name: str) -> int | None:
    if val is None:
        return None
    try:
        v = int(val)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{name} must be an integer")
    if v < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{name} must be non-negative")
    return v


@router.get("/ui/api/users/{user_id}")
async def get_user(
    request: Request,
    user_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.USER_READ)
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT user_id, team_id, organization_id, max_budget, spend,
               rpm_limit, tpm_limit, max_parallel_requests,
               rph_limit, rpd_limit, tpd_limit,
               blocked, created_at, updated_at
        FROM deltallm_usertable
        WHERE user_id = $1
        LIMIT 1
        """,
        user_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user = dict(rows[0])
    if not scope.is_platform_admin:
        org_id = str(user.get("organization_id") or "").strip()
        team_id = str(user.get("team_id") or "").strip()
        if org_id not in scope.org_ids and team_id not in scope.team_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    return user


@router.put("/ui/api/users/{user_id}")
async def update_user(
    request: Request,
    user_id: str,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.USER_UPDATE)
    db = db_or_503(request)
    await _require_runtime_user_access(request, scope, db, user_id, write=True)

    existing_rows = await db.query_raw(
        """
        SELECT user_id, team_id, organization_id, max_budget, spend,
               rpm_limit, tpm_limit, max_parallel_requests,
               rph_limit, rpd_limit, tpd_limit,
               blocked, created_at, updated_at
        FROM deltallm_usertable
        WHERE user_id = $1
        LIMIT 1
        """,
        user_id,
    )
    if not existing_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    existing = dict(existing_rows[0])

    rpm_limit = _optional_int(payload.get("rpm_limit", existing.get("rpm_limit")), "rpm_limit")
    tpm_limit = _optional_int(payload.get("tpm_limit", existing.get("tpm_limit")), "tpm_limit")
    max_parallel_requests = _optional_int(payload.get("max_parallel_requests", existing.get("max_parallel_requests")), "max_parallel_requests")
    rph_limit = _optional_int(payload.get("rph_limit", existing.get("rph_limit")), "rph_limit")
    rpd_limit = _optional_int(payload.get("rpd_limit", existing.get("rpd_limit")), "rpd_limit")
    tpd_limit = _optional_int(payload.get("tpd_limit", existing.get("tpd_limit")), "tpd_limit")
    max_budget = payload.get("max_budget", existing.get("max_budget"))
    blocked = payload.get("blocked", existing.get("blocked", False))

    await db.execute_raw(
        """
        UPDATE deltallm_usertable SET
            rpm_limit = $2,
            tpm_limit = $3,
            max_parallel_requests = $4,
            rph_limit = $5,
            rpd_limit = $6,
            tpd_limit = $7,
            max_budget = $8,
            blocked = $9,
            updated_at = NOW()
        WHERE user_id = $1
        """,
        user_id,
        rpm_limit,
        tpm_limit,
        max_parallel_requests,
        rph_limit,
        rpd_limit,
        tpd_limit,
        max_budget,
        blocked,
    )

    key_service = getattr(request.app.state, "key_service", None)
    if key_service:
        await key_service.invalidate_keys_for_user(user_id)

    updated_rows = await db.query_raw(
        """
        SELECT user_id, team_id, organization_id, max_budget, spend,
               rpm_limit, tpm_limit, max_parallel_requests,
               rph_limit, rpd_limit, tpd_limit,
               blocked, created_at, updated_at
        FROM deltallm_usertable
        WHERE user_id = $1
        LIMIT 1
        """,
        user_id,
    )
    result = dict(updated_rows[0]) if updated_rows else {}

    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_USER_UPDATE,
        scope=scope,
        resource_type="user",
        resource_id=user_id,
        request_payload=payload,
        response_payload=result,
    )
    return result


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
    asset_access_payload = {
        "scope_type": "user",
        "scope_id": user_id,
        "organization_id": organization_id,
        "team_id": team_id,
        "user_id": user_id,
        "mode": payload.get("mode"),
        "selected_callable_keys": payload.get("selected_callable_keys", []),
        "select_all_selectable": bool(payload.get("select_all_selectable", False)),
    }
    if "selected_access_group_keys" in payload:
        asset_access_payload["selected_access_group_keys"] = payload["selected_access_group_keys"]
    response = await apply_scope_asset_access(request, **asset_access_payload)
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
