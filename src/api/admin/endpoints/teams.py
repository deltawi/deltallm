from __future__ import annotations

import json
import secrets
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from src.auth.roles import Permission, ORG_ROLE_PERMISSIONS, TEAM_ROLE_PERMISSIONS, TeamRole, validate_team_role
from src.audit import AuditAction
from src.api.admin.endpoints.common import (
    AuthScope,
    db_or_503,
    emit_admin_mutation_audit,
    get_auth_scope,
    optional_int,
    to_json_value,
    validate_runtime_user_scope,
)
from src.middleware.platform_auth import get_platform_auth_context
from src.services.asset_visibility_preview import build_asset_visibility_preview
from src.services.scoped_asset_access import apply_scope_asset_access, build_scope_asset_access
from src.services.ui_authorization import build_team_capabilities

router = APIRouter(tags=["Admin Teams"])


def _team_response_payload(team: dict[str, Any], *, capabilities: dict[str, bool] | None = None) -> dict[str, Any]:
    payload = to_json_value(dict(team))
    if isinstance(payload, dict):
        payload.pop("models", None)
        if capabilities is not None:
            payload["capabilities"] = capabilities
    return payload


def _reject_legacy_models_field(payload: dict[str, Any]) -> None:
    if "models" in payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="models is no longer supported; use callable-target bindings instead",
        )


def _validate_model_limit_dict(value: Any, field_name: str) -> dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be an object mapping model names to integer limits",
        )
    result: dict[str, int] = {}
    for k, v in value.items():
        if not isinstance(k, str) or not k.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} keys must be non-empty strings")
        try:
            int_val = int(v)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} values must be integers")
        if int_val < 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} values must be non-negative")
        result[k.strip()] = int_val
    return result if result else None


def _resolve_self_service_policy(
    payload: dict[str, Any],
    *,
    existing_team: dict[str, Any] | None = None,
    default_enabled: bool,
) -> tuple[bool, int | None, float | None, bool, int | None]:
    enabled_default = existing_team.get("self_service_keys_enabled", default_enabled) if existing_team is not None else default_enabled
    max_keys_default = existing_team.get("self_service_max_keys_per_user") if existing_team is not None else None
    budget_default = existing_team.get("self_service_budget_ceiling") if existing_team is not None else None
    require_expiry_default = (
        existing_team.get("self_service_require_expiry", False) if existing_team is not None else False
    )
    max_expiry_days_default = existing_team.get("self_service_max_expiry_days") if existing_team is not None else None

    enabled = bool(payload.get("self_service_keys_enabled", enabled_default))
    max_keys = optional_int(payload.get("self_service_max_keys_per_user", max_keys_default), "self_service_max_keys_per_user")
    budget_ceiling = payload.get("self_service_budget_ceiling", budget_default)
    if budget_ceiling is not None:
        try:
            budget_ceiling = float(budget_ceiling)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="self_service_budget_ceiling must be a number")
    require_expiry = bool(payload.get("self_service_require_expiry", require_expiry_default))
    max_expiry_days = optional_int(
        payload.get("self_service_max_expiry_days", max_expiry_days_default),
        "self_service_max_expiry_days",
    )
    return enabled, max_keys, budget_ceiling, require_expiry, max_expiry_days


async def _require_team_access(
    request: Request,
    scope: AuthScope,
    db: Any,
    team_id: str,
    *,
    write: bool = False,
) -> dict[str, Any]:
    rows = await db.query_raw(
        """
        SELECT team_id, team_alias, organization_id, max_budget, spend, rpm_limit, tpm_limit, rph_limit, rpd_limit, tpd_limit,
               model_rpm_limit, model_tpm_limit, blocked,
               self_service_keys_enabled, self_service_max_keys_per_user, self_service_budget_ceiling,
               self_service_require_expiry, self_service_max_expiry_days,
               created_at, updated_at
        FROM deltallm_teamtable
        WHERE team_id = $1
        LIMIT 1
        """,
        team_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    team = dict(rows[0])
    if scope.is_platform_admin:
        return team

    required_perm = Permission.TEAM_UPDATE if write else Permission.TEAM_READ
    team_org = team.get("organization_id")

    ctx = get_platform_auth_context(request)
    if ctx:
        if team_org:
            for membership in ctx.organization_memberships:
                if str(membership.get("organization_id")) != team_org:
                    continue
                role = str(membership.get("role") or "")
                if required_perm in ORG_ROLE_PERMISSIONS.get(role, set()):
                    return team

        for membership in ctx.team_memberships:
            if str(membership.get("team_id")) != team_id:
                continue
            role = str(membership.get("role") or "")
            if required_perm in TEAM_ROLE_PERMISSIONS.get(role, set()):
                return team

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")


@router.get("/ui/api/teams")
async def list_teams(
    request: Request,
    search: str | None = Query(default=None),
    organization_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.TEAM_READ)
    db = db_or_503(request)

    clauses: list[str] = []
    params: list[Any] = []

    if not scope.is_platform_admin:
        scope_clauses: list[str] = []
        if scope.org_ids:
            ph = ", ".join(f"${len(params) + i + 1}" for i in range(len(scope.org_ids)))
            params.extend(scope.org_ids)
            scope_clauses.append(f"t.organization_id IN ({ph})")
        if scope.team_ids:
            ph = ", ".join(f"${len(params) + i + 1}" for i in range(len(scope.team_ids)))
            params.extend(scope.team_ids)
            scope_clauses.append(f"t.team_id IN ({ph})")
        if not scope_clauses:
            return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}
        clauses.append("(" + " OR ".join(scope_clauses) + ")")

    if search:
        params.append(f"%{search}%")
        clauses.append(f"(t.team_alias ILIKE ${len(params)} OR t.team_id ILIKE ${len(params)})")
    if organization_id:
        params.append(organization_id)
        clauses.append(f"t.organization_id = ${len(params)}")

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    select_cols = """t.team_id, t.team_alias, t.organization_id, t.max_budget, t.spend, t.rpm_limit, t.tpm_limit,
                   t.rph_limit, t.rpd_limit, t.tpd_limit,
                   t.model_rpm_limit, t.model_tpm_limit, t.blocked,
                   t.self_service_keys_enabled, t.self_service_max_keys_per_user,
                   t.self_service_budget_ceiling, t.self_service_require_expiry, t.self_service_max_expiry_days,
                   t.created_at, t.updated_at,
                   (SELECT COUNT(*) FROM deltallm_teammembership tm WHERE tm.team_id = t.team_id) AS member_count"""

    count_rows = await db.query_raw(
        f"SELECT COUNT(*) AS total FROM deltallm_teamtable t {where_sql}",
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)

    params.append(limit)
    params.append(offset)
    rows = await db.query_raw(
        f"""
        SELECT {select_cols}
        FROM deltallm_teamtable t
        {where_sql}
        ORDER BY t.created_at DESC
        LIMIT ${len(params) - 1} OFFSET ${len(params)}
        """,
        *params,
    )

    return {
        "data": [
            _team_response_payload(
                dict(row),
                capabilities=build_team_capabilities(scope, dict(row)),
            )
            for row in rows
        ],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.get("/ui/api/teams/{team_id}")
async def get_team(
    request: Request,
    team_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key)
    db = db_or_503(request)
    team = await _require_team_access(request, scope, db, team_id)
    return _team_response_payload(team, capabilities=build_team_capabilities(scope, team))


@router.get("/ui/api/teams/{team_id}/asset-visibility")
async def get_team_asset_visibility(
    request: Request,
    team_id: str,
    user_id: str | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.TEAM_READ)
    db = db_or_503(request)
    team = await _require_team_access(request, scope, db, team_id)
    organization_id = str(team.get("organization_id") or "").strip()
    if not organization_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Team organization is not configured")
    user_row = (
        await validate_runtime_user_scope(db, user_id, team_id=team_id)
        if user_id is not None and str(user_id).strip()
        else None
    )
    return await build_asset_visibility_preview(
        request,
        organization_id=organization_id,
        team_id=team_id,
        user_id=str(user_row.get("user_id") or "").strip() or None if user_row else None,
    )


@router.get("/ui/api/teams/{team_id}/asset-access")
async def get_team_asset_access(
    request: Request,
    team_id: str,
    include_targets: bool = Query(default=True),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.TEAM_READ)
    db = db_or_503(request)
    team = await _require_team_access(request, scope, db, team_id)
    organization_id = str(team.get("organization_id") or "").strip()
    if not organization_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Team organization is not configured")
    return await build_scope_asset_access(
        request,
        scope_type="team",
        scope_id=team_id,
        organization_id=organization_id,
        team_id=team_id,
        include_targets=include_targets,
    )


@router.put("/ui/api/teams/{team_id}/asset-access")
async def update_team_asset_access(
    request: Request,
    team_id: str,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.TEAM_UPDATE)
    db = db_or_503(request)
    team = await _require_team_access(request, scope, db, team_id, write=True)
    organization_id = str(team.get("organization_id") or "").strip()
    if not organization_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Team organization is not configured")
    response = await apply_scope_asset_access(
        request,
        scope_type="team",
        scope_id=team_id,
        organization_id=organization_id,
        team_id=team_id,
        mode=payload.get("mode"),
        selected_callable_keys=payload.get("selected_callable_keys", []),
        select_all_selectable=bool(payload.get("select_all_selectable", False)),
    )
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TEAM_ASSET_ACCESS_UPDATE,
        scope=scope,
        resource_type="team_asset_access",
        resource_id=team_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.post("/ui/api/teams")
async def create_team(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    _reject_legacy_models_field(payload)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.TEAM_UPDATE)
    organization_id = payload.get("organization_id")
    if not organization_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="organization_id is required")
    if not scope.is_platform_admin:
        if organization_id not in scope.org_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only create teams in your own organizations")
    db = db_or_503(request)
    team_id = str(payload.get("team_id") or f"team-{secrets.token_hex(6)}")
    team_alias = payload.get("team_alias")
    max_budget = payload.get("max_budget")
    rpm_limit = optional_int(payload.get("rpm_limit"), "rpm_limit")
    tpm_limit = optional_int(payload.get("tpm_limit"), "tpm_limit")
    rph_limit = optional_int(payload.get("rph_limit"), "rph_limit")
    rpd_limit = optional_int(payload.get("rpd_limit"), "rpd_limit")
    tpd_limit = optional_int(payload.get("tpd_limit"), "tpd_limit")
    model_rpm_limit = _validate_model_limit_dict(payload.get("model_rpm_limit"), "model_rpm_limit")
    model_tpm_limit = _validate_model_limit_dict(payload.get("model_tpm_limit"), "model_tpm_limit")
    ss_keys_enabled, ss_max_keys, ss_budget_ceiling, ss_require_expiry, ss_max_expiry_days = _resolve_self_service_policy(
        payload,
        default_enabled=True,
    )

    await db.execute_raw(
        """
        INSERT INTO deltallm_teamtable (
            team_id, team_alias, organization_id, max_budget, spend,
            rpm_limit, tpm_limit, rph_limit, rpd_limit, tpd_limit,
            model_rpm_limit, model_tpm_limit, blocked,
            self_service_keys_enabled, self_service_max_keys_per_user,
            self_service_budget_ceiling, self_service_require_expiry, self_service_max_expiry_days,
            created_at, updated_at
        )
        VALUES ($1, $2, $3, $4, 0, $5, $6, $7, $8, $9, $10::jsonb, $11::jsonb, false, $12, $13, $14, $15, $16, NOW(), NOW())
        """,
        team_id,
        team_alias,
        organization_id,
        max_budget,
        rpm_limit,
        tpm_limit,
        rph_limit,
        rpd_limit,
        tpd_limit,
        json.dumps(model_rpm_limit) if model_rpm_limit else None,
        json.dumps(model_tpm_limit) if model_tpm_limit else None,
        ss_keys_enabled,
        ss_max_keys,
        ss_budget_ceiling,
        ss_require_expiry,
        ss_max_expiry_days,
    )

    response = {
        "team_id": team_id,
        "team_alias": team_alias,
        "organization_id": organization_id,
        "max_budget": max_budget,
        "rpm_limit": rpm_limit,
        "tpm_limit": tpm_limit,
        "rph_limit": rph_limit,
        "rpd_limit": rpd_limit,
        "tpd_limit": tpd_limit,
        "model_rpm_limit": model_rpm_limit,
        "model_tpm_limit": model_tpm_limit,
        "self_service_keys_enabled": ss_keys_enabled,
        "self_service_max_keys_per_user": ss_max_keys,
        "self_service_budget_ceiling": ss_budget_ceiling,
        "self_service_require_expiry": ss_require_expiry,
        "self_service_max_expiry_days": ss_max_expiry_days,
        "blocked": False,
    }
    response["capabilities"] = build_team_capabilities(scope, response)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TEAM_CREATE,
        scope=scope,
        resource_type="team",
        resource_id=team_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.put("/ui/api/teams/{team_id}")
async def update_team(
    request: Request,
    team_id: str,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    _reject_legacy_models_field(payload)
    scope = get_auth_scope(request, authorization, x_master_key)
    db = db_or_503(request)
    existing_team = await _require_team_access(request, scope, db, team_id, write=True)

    team_alias = payload.get("team_alias", existing_team.get("team_alias"))
    organization_id = payload.get("organization_id", existing_team.get("organization_id"))
    if not organization_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="organization_id is required")
    if not scope.is_platform_admin and organization_id not in scope.org_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot move team to an organization you don't manage")
    max_budget = payload.get("max_budget", existing_team.get("max_budget"))
    rpm_limit = optional_int(payload.get("rpm_limit", existing_team.get("rpm_limit")), "rpm_limit")
    tpm_limit = optional_int(payload.get("tpm_limit", existing_team.get("tpm_limit")), "tpm_limit")
    rph_limit = optional_int(payload.get("rph_limit", existing_team.get("rph_limit")), "rph_limit")
    rpd_limit = optional_int(payload.get("rpd_limit", existing_team.get("rpd_limit")), "rpd_limit")
    tpd_limit = optional_int(payload.get("tpd_limit", existing_team.get("tpd_limit")), "tpd_limit")
    model_rpm_limit = _validate_model_limit_dict(
        payload.get("model_rpm_limit", existing_team.get("model_rpm_limit")), "model_rpm_limit"
    )
    model_tpm_limit = _validate_model_limit_dict(
        payload.get("model_tpm_limit", existing_team.get("model_tpm_limit")), "model_tpm_limit"
    )
    ss_keys_enabled, ss_max_keys, ss_budget_ceiling, ss_require_expiry, ss_max_expiry_days = _resolve_self_service_policy(
        payload,
        existing_team=existing_team,
        default_enabled=False,
    )

    await db.execute_raw(
        """
        UPDATE deltallm_teamtable
        SET team_alias = $1,
            organization_id = $2,
            max_budget = $3,
            rpm_limit = $4,
            tpm_limit = $5,
            rph_limit = $6,
            rpd_limit = $7,
            tpd_limit = $8,
            model_rpm_limit = $9::jsonb,
            model_tpm_limit = $10::jsonb,
            self_service_keys_enabled = $11,
            self_service_max_keys_per_user = $12,
            self_service_budget_ceiling = $13,
            self_service_require_expiry = $14,
            self_service_max_expiry_days = $15,
            updated_at = NOW()
        WHERE team_id = $16
        """,
        team_alias,
        organization_id,
        max_budget,
        rpm_limit,
        tpm_limit,
        rph_limit,
        rpd_limit,
        tpd_limit,
        json.dumps(model_rpm_limit) if model_rpm_limit else None,
        json.dumps(model_tpm_limit) if model_tpm_limit else None,
        ss_keys_enabled,
        ss_max_keys,
        ss_budget_ceiling,
        ss_require_expiry,
        ss_max_expiry_days,
        team_id,
    )
    updated_rows = await db.query_raw(
        """
        SELECT team_id, team_alias, organization_id, max_budget, spend, rpm_limit, tpm_limit, rph_limit, rpd_limit, tpd_limit,
               model_rpm_limit, model_tpm_limit, blocked,
               self_service_keys_enabled, self_service_max_keys_per_user, self_service_budget_ceiling,
               self_service_require_expiry, self_service_max_expiry_days,
               created_at, updated_at
        FROM deltallm_teamtable
        WHERE team_id = $1
        LIMIT 1
        """,
        team_id,
    )
    if not updated_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    updated_team = dict(updated_rows[0])
    updated = _team_response_payload(
        updated_team,
        capabilities=build_team_capabilities(scope, updated_team),
    )
    key_service = getattr(request.app.state, "key_service", None)
    if key_service is not None:
        try:
            await key_service.invalidate_keys_for_team(team_id)
        except Exception:
            pass
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TEAM_UPDATE,
        scope=scope,
        resource_type="team",
        resource_id=team_id,
        request_payload=payload,
        response_payload=updated if isinstance(updated, dict) else None,
        before=to_json_value(existing_team),
        after=updated if isinstance(updated, dict) else None,
    )
    return updated


@router.get("/ui/api/teams/{team_id}/members")
async def list_team_members(
    request: Request,
    team_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> list[dict[str, Any]]:
    scope = get_auth_scope(request, authorization, x_master_key)
    db = db_or_503(request)
    await _require_team_access(request, scope, db, team_id)
    rows = await db.query_raw(
        """
        SELECT
            tm.membership_id,
            tm.account_id AS user_id,
            pa.email AS user_email,
            tm.role AS user_role,
            tm.team_id,
            tm.created_at,
            tm.updated_at
        FROM deltallm_teammembership tm
        JOIN deltallm_platformaccount pa
          ON pa.account_id = tm.account_id
        WHERE tm.team_id = $1
        ORDER BY tm.created_at DESC
        """,
        team_id,
    )
    return [to_json_value(dict(row)) for row in rows]


@router.get("/ui/api/teams/{team_id}/member-candidates")
async def list_team_member_candidates(
    request: Request,
    team_id: str,
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> list[dict[str, Any]]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.TEAM_READ)
    db = db_or_503(request)
    team = await _require_team_access(request, scope, db, team_id)
    organization_id = team.get("organization_id")
    if not organization_id:
        return []

    clauses = [
        "om.organization_id = $1",
    ]
    params: list[Any] = [organization_id, team_id]
    if search and search.strip():
        params.append(f"%{search.strip()}%")
        clauses.append(f"(pa.email ILIKE ${len(params)} OR pa.account_id::text ILIKE ${len(params)})")
    params.append(limit)

    where_sql = " AND ".join(clauses)
    rows = await db.query_raw(
        f"""
        SELECT
            pa.account_id,
            pa.email,
            pa.role,
            pa.is_active,
            pa.created_at,
            pa.updated_at,
            om.role AS organization_role,
            tm.membership_id AS team_membership_id,
            tm.role AS team_role,
            (tm.membership_id IS NOT NULL) AS already_member
        FROM deltallm_platformaccount pa
        JOIN deltallm_organizationmembership om
          ON om.account_id = pa.account_id
        LEFT JOIN deltallm_teammembership tm
          ON tm.account_id = pa.account_id
         AND tm.team_id = $2
        WHERE {where_sql}
        ORDER BY pa.email ASC, pa.account_id ASC
        LIMIT ${len(params)}
        """,
        *params,
    )
    return [to_json_value(dict(row)) for row in rows]


@router.post("/ui/api/teams/{team_id}/members")
async def add_team_member(
    request: Request,
    team_id: str,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key)
    db = db_or_503(request)
    team = await _require_team_access(request, scope, db, team_id, write=True)
    account_id = str(payload.get("account_id") or payload.get("user_id") or "").strip()
    try:
        user_role = validate_team_role(payload.get("user_role") or TeamRole.VIEWER)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="account_id is required")

    account_rows = await db.query_raw(
        "SELECT account_id, email FROM deltallm_platformaccount WHERE account_id = $1 LIMIT 1",
        account_id,
    )
    if not account_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    organization_id = team.get("organization_id")
    if organization_id:
        org_membership_rows = await db.query_raw(
            "SELECT membership_id FROM deltallm_organizationmembership WHERE organization_id = $1 AND account_id = $2 LIMIT 1",
            organization_id,
            account_id,
        )
        if not org_membership_rows:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account is not a member of this team's organization")

    await db.execute_raw(
        """
        INSERT INTO deltallm_teammembership (membership_id, account_id, team_id, role, created_at, updated_at)
        VALUES (gen_random_uuid(), $1, $2, $3, NOW(), NOW())
        ON CONFLICT (account_id, team_id)
        DO UPDATE SET role = EXCLUDED.role, updated_at = NOW()
        """,
        account_id,
        team_id,
        user_role,
    )
    response = {
        "user_id": account_id,
        "user_email": account_rows[0].get("email"),
        "user_role": user_role,
        "team_id": team_id,
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TEAM_MEMBER_ADD,
        scope=scope,
        resource_type="team_membership",
        resource_id=f"{team_id}:{account_id}",
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.delete("/ui/api/teams/{team_id}")
async def delete_team(
    request: Request,
    team_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, bool]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key)
    db = db_or_503(request)
    await _require_team_access(request, scope, db, team_id, write=True)
    key_count = await db.query_raw(
        "SELECT COUNT(*) AS cnt FROM deltallm_verificationtoken WHERE team_id = $1",
        team_id,
    )
    if key_count and int(key_count[0].get("cnt", 0)) > 0:
        raise HTTPException(status_code=409, detail=f"Cannot delete team: {key_count[0]['cnt']} API key(s) still assigned. Reassign or revoke them first.")
    await db.execute_raw(
        "DELETE FROM deltallm_teammembership WHERE team_id = $1",
        team_id,
    )
    await db.execute_raw(
        "UPDATE deltallm_usertable SET team_id = NULL, updated_at = NOW() WHERE team_id = $1",
        team_id,
    )
    deleted = await db.execute_raw(
        "DELETE FROM deltallm_teamtable WHERE team_id = $1",
        team_id,
    )
    response = {"deleted": int(deleted or 0) > 0}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TEAM_DELETE,
        scope=scope,
        resource_type="team",
        resource_id=team_id,
        response_payload=response,
    )
    return response


@router.delete("/ui/api/teams/{team_id}/members/{user_id}")
async def remove_team_member(
    request: Request,
    team_id: str,
    user_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, bool]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key)
    db = db_or_503(request)
    await _require_team_access(request, scope, db, team_id, write=True)
    removed = await db.execute_raw(
        "DELETE FROM deltallm_teammembership WHERE team_id = $1 AND account_id = $2",
        team_id,
        user_id,
    )

    revoked_keys = 0
    if int(removed or 0) > 0:
        owned_key_rows = await db.query_raw(
            "SELECT token FROM deltallm_verificationtoken WHERE team_id = $1 AND owner_account_id = $2",
            team_id,
            user_id,
        )
        if owned_key_rows:
            revoked_keys = int(
                await db.execute_raw(
                    "DELETE FROM deltallm_verificationtoken WHERE team_id = $1 AND owner_account_id = $2",
                    team_id,
                    user_id,
                )
                or 0
            )
            if revoked_keys > 0:
                try:
                    key_service = request.app.state.key_service
                    for kr in owned_key_rows:
                        await key_service.invalidate_key_cache_by_hash(str(kr["token"]))
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception("failed to invalidate key cache after membership removal auto-revoke")
                for kr in owned_key_rows:
                    await emit_admin_mutation_audit(
                        request=request,
                        request_start=request_start,
                        action=AuditAction.ADMIN_KEY_AUTO_REVOKE_MEMBERSHIP_REMOVED,
                        scope=scope,
                        resource_type="api_key",
                        resource_id=str(kr["token"]),
                        response_payload={"team_id": team_id, "removed_account_id": user_id},
                    )

    response = {"removed": int(removed or 0) > 0, "revoked_keys": revoked_keys}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_TEAM_MEMBER_REMOVE,
        scope=scope,
        resource_type="team_membership",
        resource_id=f"{team_id}:{user_id}",
        response_payload=response,
    )
    return response
