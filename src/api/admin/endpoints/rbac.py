from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.auth.roles import OrganizationRole, Permission, TeamRole
from src.api.admin.endpoints.common import db_or_503, to_json_value
from src.middleware.admin import require_admin_permission

router = APIRouter(tags=["Admin RBAC"])


def _require_valid_role(role: str, allowed: set[str], field_name: str) -> str:
    if role not in allowed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"invalid {field_name}")
    return role


@router.get("/ui/api/rbac/accounts", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def list_rbac_accounts(request: Request) -> list[dict[str, Any]]:
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT account_id, email, role, is_active, force_password_change, mfa_enabled, created_at, updated_at, last_login_at
        FROM litellm_platformaccount
        ORDER BY created_at DESC
        """
    )
    return [to_json_value(dict(row)) for row in rows]


@router.post("/ui/api/rbac/accounts", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def upsert_rbac_account(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    db = db_or_503(request)
    service = getattr(request.app.state, "platform_identity_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auth service unavailable")

    email = str(payload.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="email is required")

    role = str(payload.get("role") or "org_user")
    role = _require_valid_role(
        role,
        {"platform_admin", "platform_co_admin", "org_user"},
        "role",
    )
    is_active = bool(payload.get("is_active", True))
    password = payload.get("password")

    await db.execute_raw(
        """
        INSERT INTO litellm_platformaccount (
            account_id, email, role, is_active, force_password_change, mfa_enabled, created_at, updated_at
        )
        VALUES (gen_random_uuid(), $1, $2, $3, false, false, NOW(), NOW())
        ON CONFLICT (email)
        DO UPDATE SET role = EXCLUDED.role, is_active = EXCLUDED.is_active, updated_at = NOW()
        """,
        email,
        role,
        is_active,
    )

    if isinstance(password, str) and password:
        rows = await db.query_raw(
            "SELECT account_id FROM litellm_platformaccount WHERE lower(email)=lower($1) LIMIT 1",
            email,
        )
        if rows:
            await service.change_password(account_id=rows[0]["account_id"], new_password=password, current_password=None)

    rows = await db.query_raw(
        """
        SELECT account_id, email, role, is_active, force_password_change, mfa_enabled, created_at, updated_at, last_login_at
        FROM litellm_platformaccount
        WHERE lower(email)=lower($1)
        LIMIT 1
        """,
        email,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="account upsert failed")
    return to_json_value(dict(rows[0]))


@router.get("/ui/api/rbac/organization-memberships", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def list_org_memberships(request: Request, account_id: str | None = None) -> list[dict[str, Any]]:
    db = db_or_503(request)
    if account_id:
        rows = await db.query_raw(
            """
            SELECT membership_id, account_id, organization_id, role, created_at, updated_at
            FROM litellm_organizationmembership
            WHERE account_id = $1
            ORDER BY created_at DESC
            """,
            account_id,
        )
    else:
        rows = await db.query_raw(
            """
            SELECT membership_id, account_id, organization_id, role, created_at, updated_at
            FROM litellm_organizationmembership
            ORDER BY created_at DESC
            """
        )
    return [to_json_value(dict(row)) for row in rows]


@router.post("/ui/api/rbac/organization-memberships", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def upsert_org_membership(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    db = db_or_503(request)
    account_id = payload.get("account_id")
    email = payload.get("email")
    organization_id = str(payload.get("organization_id") or "").strip()
    role = str(payload.get("role") or OrganizationRole.MEMBER)
    role = _require_valid_role(
        role,
        {OrganizationRole.MEMBER, OrganizationRole.OWNER, OrganizationRole.ADMIN, OrganizationRole.BILLING, OrganizationRole.AUDITOR},
        "organization role",
    )

    if not organization_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="organization_id is required")

    if not account_id and email:
        rows = await db.query_raw("SELECT account_id FROM litellm_platformaccount WHERE lower(email)=lower($1) LIMIT 1", email)
        if rows:
            account_id = rows[0].get("account_id")
    if not account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="account_id or known email is required")

    await db.execute_raw(
        """
        INSERT INTO litellm_organizationmembership (membership_id, account_id, organization_id, role, created_at, updated_at)
        VALUES (gen_random_uuid(), $1, $2, $3, NOW(), NOW())
        ON CONFLICT (account_id, organization_id)
        DO UPDATE SET role = EXCLUDED.role, updated_at = NOW()
        """,
        account_id,
        organization_id,
        role,
    )

    rows = await db.query_raw(
        """
        SELECT membership_id, account_id, organization_id, role, created_at, updated_at
        FROM litellm_organizationmembership
        WHERE account_id = $1 AND organization_id = $2
        LIMIT 1
        """,
        account_id,
        organization_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="membership upsert failed")
    return to_json_value(dict(rows[0]))


@router.get("/ui/api/rbac/team-memberships", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def list_team_memberships(request: Request, account_id: str | None = None) -> list[dict[str, Any]]:
    db = db_or_503(request)
    if account_id:
        rows = await db.query_raw(
            """
            SELECT membership_id, account_id, team_id, role, created_at, updated_at
            FROM litellm_teammembership
            WHERE account_id = $1
            ORDER BY created_at DESC
            """,
            account_id,
        )
    else:
        rows = await db.query_raw(
            """
            SELECT membership_id, account_id, team_id, role, created_at, updated_at
            FROM litellm_teammembership
            ORDER BY created_at DESC
            """
        )
    return [to_json_value(dict(row)) for row in rows]


@router.post("/ui/api/rbac/team-memberships", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def upsert_team_membership(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    db = db_or_503(request)
    account_id = payload.get("account_id")
    email = payload.get("email")
    team_id = str(payload.get("team_id") or "").strip()
    role = str(payload.get("role") or TeamRole.VIEWER)
    role = _require_valid_role(
        role,
        {TeamRole.ADMIN, TeamRole.DEVELOPER, TeamRole.VIEWER},
        "team role",
    )

    if not team_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="team_id is required")

    if not account_id and email:
        rows = await db.query_raw("SELECT account_id FROM litellm_platformaccount WHERE lower(email)=lower($1) LIMIT 1", email)
        if rows:
            account_id = rows[0].get("account_id")
    if not account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="account_id or known email is required")

    await db.execute_raw(
        """
        INSERT INTO litellm_teammembership (membership_id, account_id, team_id, role, created_at, updated_at)
        VALUES (gen_random_uuid(), $1, $2, $3, NOW(), NOW())
        ON CONFLICT (account_id, team_id)
        DO UPDATE SET role = EXCLUDED.role, updated_at = NOW()
        """,
        account_id,
        team_id,
        role,
    )

    rows = await db.query_raw(
        """
        SELECT membership_id, account_id, team_id, role, created_at, updated_at
        FROM litellm_teammembership
        WHERE account_id = $1 AND team_id = $2
        LIMIT 1
        """,
        account_id,
        team_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="membership upsert failed")
    return to_json_value(dict(rows[0]))
