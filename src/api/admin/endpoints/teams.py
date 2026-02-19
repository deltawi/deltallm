from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status

from src.auth.roles import Permission, ORG_ROLE_PERMISSIONS, TEAM_ROLE_PERMISSIONS
from src.api.admin.endpoints.common import db_or_503, optional_int, to_json_value, get_auth_scope, AuthScope
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
        FROM litellm_teamtable
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
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> list[dict[str, Any]]:
    scope = get_auth_scope(request, authorization, x_master_key)
    db = db_or_503(request)

    if scope.is_platform_admin:
        rows = await db.query_raw(
            """
            SELECT t.team_id, t.team_alias, t.organization_id, t.max_budget, t.spend, t.models, t.rpm_limit, t.tpm_limit, t.blocked,
                   t.created_at, t.updated_at,
                   (SELECT COUNT(*) FROM litellm_usertable u WHERE u.team_id = t.team_id) AS member_count
            FROM litellm_teamtable t
            ORDER BY t.created_at DESC
            """
        )
    elif scope.org_ids:
        placeholders = ", ".join(f"${i+1}" for i in range(len(scope.org_ids)))
        rows = await db.query_raw(
            f"""
            SELECT t.team_id, t.team_alias, t.organization_id, t.max_budget, t.spend, t.models, t.rpm_limit, t.tpm_limit, t.blocked,
                   t.created_at, t.updated_at,
                   (SELECT COUNT(*) FROM litellm_usertable u WHERE u.team_id = t.team_id) AS member_count
            FROM litellm_teamtable t
            WHERE t.organization_id IN ({placeholders})
            ORDER BY t.created_at DESC
            """,
            *scope.org_ids,
        )
    else:
        rows = []

    return [to_json_value(dict(row)) for row in rows]


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
    scope = get_auth_scope(request, authorization, x_master_key)
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
        INSERT INTO litellm_teamtable (team_id, team_alias, organization_id, max_budget, spend, rpm_limit, tpm_limit, models, blocked, created_at, updated_at)
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
            "SELECT user_id, team_id FROM litellm_usertable WHERE user_id = $1 LIMIT 1",
            ctx.account_id,
        )
        if not existing:
            await db.execute_raw(
                """
                INSERT INTO litellm_usertable (user_id, user_email, user_role, spend, models, team_id, created_at, updated_at)
                VALUES ($1, $2, 'team_admin', 0, '{}'::text[], $3, NOW(), NOW())
                """,
                ctx.account_id,
                ctx.email,
                team_id,
            )
        elif not existing[0].get("team_id"):
            await db.execute_raw(
                "UPDATE litellm_usertable SET team_id = $1, user_role = 'team_admin', updated_at = NOW() WHERE user_id = $2",
                team_id,
                ctx.account_id,
            )

    return {
        "team_id": team_id,
        "team_alias": team_alias,
        "organization_id": organization_id,
        "max_budget": max_budget,
        "rpm_limit": rpm_limit,
        "tpm_limit": tpm_limit,
        "models": models,
        "blocked": False,
    }


@router.put("/ui/api/teams/{team_id}")
async def update_team(
    request: Request,
    team_id: str,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
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
        UPDATE litellm_teamtable
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
        FROM litellm_teamtable
        WHERE team_id = $1
        LIMIT 1
        """,
        team_id,
    )
    if not updated_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return to_json_value(dict(updated_rows[0]))


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
        SELECT user_id, user_email, user_role, spend, max_budget, team_id, created_at, updated_at
        FROM litellm_usertable
        WHERE team_id = $1
        ORDER BY created_at DESC
        """,
        team_id,
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
    scope = get_auth_scope(request, authorization, x_master_key)
    db = db_or_503(request)
    await _require_team_access(request, scope, db, team_id, write=True)
    user_id = str(payload.get("user_id") or "").strip()
    user_email = payload.get("user_email")
    user_role = payload.get("user_role") or "internal_user"
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id is required")

    await db.execute_raw(
        """
        INSERT INTO litellm_usertable (user_id, user_email, user_role, spend, models, team_id, created_at, updated_at)
        VALUES ($1, $2, $3, 0, '{}'::text[], $4, NOW(), NOW())
        ON CONFLICT (user_id)
        DO UPDATE SET user_email = EXCLUDED.user_email, user_role = EXCLUDED.user_role, team_id = EXCLUDED.team_id, updated_at = NOW()
        """,
        user_id,
        user_email,
        user_role,
        team_id,
    )
    return {
        "user_id": user_id,
        "user_email": user_email,
        "user_role": user_role,
        "team_id": team_id,
    }


@router.delete("/ui/api/teams/{team_id}")
async def delete_team(
    request: Request,
    team_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, bool]:
    scope = get_auth_scope(request, authorization, x_master_key)
    db = db_or_503(request)
    await _require_team_access(request, scope, db, team_id, write=True)
    key_count = await db.query_raw(
        "SELECT COUNT(*) AS cnt FROM litellm_verificationtoken WHERE team_id = $1",
        team_id,
    )
    if key_count and int(key_count[0].get("cnt", 0)) > 0:
        raise HTTPException(status_code=409, detail=f"Cannot delete team: {key_count[0]['cnt']} API key(s) still assigned. Reassign or revoke them first.")
    await db.execute_raw(
        "UPDATE litellm_usertable SET team_id = NULL, updated_at = NOW() WHERE team_id = $1",
        team_id,
    )
    deleted = await db.execute_raw(
        "DELETE FROM litellm_teamtable WHERE team_id = $1",
        team_id,
    )
    return {"deleted": int(deleted or 0) > 0}


@router.delete("/ui/api/teams/{team_id}/members/{user_id}")
async def remove_team_member(
    request: Request,
    team_id: str,
    user_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, bool]:
    scope = get_auth_scope(request, authorization, x_master_key)
    db = db_or_503(request)
    await _require_team_access(request, scope, db, team_id, write=True)
    updated = await db.execute_raw(
        "UPDATE litellm_usertable SET team_id = NULL, updated_at = NOW() WHERE team_id = $1 AND user_id = $2",
        team_id,
        user_id,
    )
    return {"removed": int(updated or 0) > 0}
