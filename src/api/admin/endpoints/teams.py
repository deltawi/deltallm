from __future__ import annotations

import secrets
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from src.auth.roles import Permission, ORG_ROLE_PERMISSIONS, TEAM_ROLE_PERMISSIONS, TeamRole
from src.audit import AuditAction
from src.api.admin.endpoints.common import (
    db_or_503,
    emit_admin_mutation_audit,
    optional_int,
    to_json_value,
    get_auth_scope,
    AuthScope,
)
from src.middleware.platform_auth import get_platform_auth_context

router = APIRouter(tags=["Admin Teams"])


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
        SELECT team_id, team_alias, organization_id, max_budget, spend, models, rpm_limit, tpm_limit, blocked, created_at, updated_at
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
        if scope.org_ids:
            ph = ", ".join(f"${len(params) + i + 1}" for i in range(len(scope.org_ids)))
            params.extend(scope.org_ids)
            clauses.append(f"t.organization_id IN ({ph})")
        else:
            return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}

    if search:
        params.append(f"%{search}%")
        clauses.append(f"(t.team_alias ILIKE ${len(params)} OR t.team_id ILIKE ${len(params)})")
    if organization_id:
        params.append(organization_id)
        clauses.append(f"t.organization_id = ${len(params)}")

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    select_cols = """t.team_id, t.team_alias, t.organization_id, t.max_budget, t.spend, t.models, t.rpm_limit, t.tpm_limit, t.blocked,
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
        "data": [to_json_value(dict(row)) for row in rows],
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
    return to_json_value(team)


@router.post("/ui/api/teams")
async def create_team(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
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
    models = payload.get("models") if isinstance(payload.get("models"), list) else []

    await db.execute_raw(
        """
        INSERT INTO deltallm_teamtable (team_id, team_alias, organization_id, max_budget, spend, rpm_limit, tpm_limit, models, blocked, created_at, updated_at)
        VALUES ($1, $2, $3, $4, 0, $5, $6, $7::text[], false, NOW(), NOW())
        """,
        team_id,
        team_alias,
        organization_id,
        max_budget,
        rpm_limit,
        tpm_limit,
        models,
    )

    ctx = get_platform_auth_context(request)
    if ctx:
        existing = await db.query_raw(
            "SELECT user_id, team_id FROM deltallm_usertable WHERE user_id = $1 LIMIT 1",
            ctx.account_id,
        )
        if not existing:
            await db.execute_raw(
                """
                INSERT INTO deltallm_usertable (user_id, user_email, user_role, spend, models, team_id, created_at, updated_at)
                VALUES ($1, $2, 'team_admin', 0, '{}'::text[], $3, NOW(), NOW())
                """,
                ctx.account_id,
                ctx.email,
                team_id,
            )
        elif not existing[0].get("team_id"):
            await db.execute_raw(
                "UPDATE deltallm_usertable SET team_id = $1, user_role = 'team_admin', updated_at = NOW() WHERE user_id = $2",
                team_id,
                ctx.account_id,
            )

    response = {
        "team_id": team_id,
        "team_alias": team_alias,
        "organization_id": organization_id,
        "max_budget": max_budget,
        "rpm_limit": rpm_limit,
        "tpm_limit": tpm_limit,
        "models": models,
        "blocked": False,
    }
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
    models = payload.get("models", existing_team.get("models"))
    if not isinstance(models, list):
        models = existing_team.get("models") or []

    await db.execute_raw(
        """
        UPDATE deltallm_teamtable
        SET team_alias = $1,
            organization_id = $2,
            max_budget = $3,
            rpm_limit = $4,
            tpm_limit = $5,
            models = $6::text[],
            updated_at = NOW()
        WHERE team_id = $7
        """,
        team_alias,
        organization_id,
        max_budget,
        rpm_limit,
        tpm_limit,
        models,
        team_id,
    )
    updated_rows = await db.query_raw(
        """
        SELECT team_id, team_alias, organization_id, max_budget, spend, models, rpm_limit, tpm_limit, blocked, created_at, updated_at
        FROM deltallm_teamtable
        WHERE team_id = $1
        LIMIT 1
        """,
        team_id,
    )
    if not updated_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    updated = to_json_value(dict(updated_rows[0]))
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
    user_role = str(payload.get("user_role") or TeamRole.VIEWER).strip()
    allowed_roles = {TeamRole.ADMIN, TeamRole.DEVELOPER, TeamRole.VIEWER}
    if user_role not in allowed_roles:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid team role")
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
    response = {"removed": int(removed or 0) > 0}
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
