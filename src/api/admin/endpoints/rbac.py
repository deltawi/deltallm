from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.auth.roles import OrganizationRole, Permission, PlatformRole, TeamRole
from src.audit import AuditAction
from src.api.admin.endpoints.common import db_or_503, emit_admin_mutation_audit, to_json_value
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
        FROM deltallm_platformaccount
        ORDER BY created_at DESC
        """
    )
    return [to_json_value(dict(row)) for row in rows]


@router.get("/ui/api/principals", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def list_principals(
    request: Request,
    search: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    db = db_or_503(request)
    clauses: list[str] = []
    params: list[Any] = []
    if search and search.strip():
        params.append(f"%{search.strip()}%")
        clauses.append(f"(email ILIKE ${len(params)} OR role ILIKE ${len(params)} OR account_id::text ILIKE ${len(params)})")
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    count_rows = await db.query_raw(
        f"SELECT COUNT(*) AS total FROM deltallm_platformaccount{where_sql}",
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)

    page_params = [*params, limit, offset]
    account_rows = await db.query_raw(
        f"""
        SELECT account_id, email, role, is_active, force_password_change, mfa_enabled, created_at, updated_at, last_login_at
        FROM deltallm_platformaccount
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
        """,
        *page_params,
    )
    account_ids = [str(row.get("account_id") or "") for row in account_rows if row.get("account_id")]
    if not account_ids:
        return {
            "data": [],
            "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
        }

    account_ph = ", ".join(f"${i + 1}" for i in range(len(account_ids)))
    org_rows = await db.query_raw(
        f"""
        SELECT membership_id, account_id, organization_id, role, created_at, updated_at
        FROM deltallm_organizationmembership
        WHERE account_id IN ({account_ph})
        ORDER BY created_at DESC
        """,
        *account_ids,
    )
    team_rows = await db.query_raw(
        f"""
        SELECT membership_id, account_id, team_id, role, created_at, updated_at
        FROM deltallm_teammembership
        WHERE account_id IN ({account_ph})
        ORDER BY created_at DESC
        """,
        *account_ids,
    )
    runtime_user_rows = await db.query_raw(
        f"""
        SELECT pa.account_id, u.user_id
        FROM deltallm_platformaccount pa
        JOIN deltallm_usertable u ON lower(u.user_email) = lower(pa.email)
        WHERE pa.account_id IN ({account_ph})
        """,
        *account_ids,
    )

    org_by_account: dict[str, list[dict[str, Any]]] = {}
    for row in org_rows:
        item = to_json_value(dict(row))
        if not isinstance(item, dict):
            continue
        account_id = str(item.get("account_id") or "")
        if not account_id:
            continue
        org_by_account.setdefault(account_id, []).append(item)

    team_by_account: dict[str, list[dict[str, Any]]] = {}
    for row in team_rows:
        item = to_json_value(dict(row))
        if not isinstance(item, dict):
            continue
        account_id = str(item.get("account_id") or "")
        if not account_id:
            continue
        team_by_account.setdefault(account_id, []).append(item)

    runtime_user_by_account: dict[str, str] = {}
    for row in runtime_user_rows:
        item = to_json_value(dict(row))
        if not isinstance(item, dict):
            continue
        account_id = str(item.get("account_id") or "")
        runtime_user_id = str(item.get("user_id") or "")
        if not account_id or not runtime_user_id:
            continue
        runtime_user_by_account[account_id] = runtime_user_id

    principals: list[dict[str, Any]] = []
    for row in account_rows:
        base = to_json_value(dict(row))
        if not isinstance(base, dict):
            continue

        account_id = str(base.get("account_id") or "")
        org_memberships = org_by_account.get(account_id, [])
        team_memberships = team_by_account.get(account_id, [])

        principals.append(
            {
                **base,
                "runtime_user_id": runtime_user_by_account.get(account_id),
                "organization_memberships": org_memberships,
                "team_memberships": team_memberships,
            }
        )

    return {
        "data": principals,
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.post("/ui/api/rbac/accounts", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def upsert_rbac_account(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    service = getattr(request.app.state, "platform_identity_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auth service unavailable")

    email = str(payload.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="email is required")

    role = str(payload.get("role") or PlatformRole.ORG_USER)
    role = _require_valid_role(
        role,
        {PlatformRole.ADMIN, PlatformRole.ORG_USER},
        "role",
    )
    is_active = bool(payload.get("is_active", True))
    password = payload.get("password")

    await db.execute_raw(
        """
        INSERT INTO deltallm_platformaccount (
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
            "SELECT account_id FROM deltallm_platformaccount WHERE lower(email)=lower($1) LIMIT 1",
            email,
        )
        if rows:
            await service.change_password(account_id=rows[0]["account_id"], new_password=password, current_password=None)

    rows = await db.query_raw(
        """
        SELECT account_id, email, role, is_active, force_password_change, mfa_enabled, created_at, updated_at, last_login_at
        FROM deltallm_platformaccount
        WHERE lower(email)=lower($1)
        LIMIT 1
        """,
        email,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="account upsert failed")
    response = to_json_value(dict(rows[0]))
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_RBAC_ACCOUNT_UPSERT,
        resource_type="platform_account",
        resource_id=str(rows[0].get("account_id") or ""),
        request_payload=payload,
        response_payload=response if isinstance(response, dict) else None,
    )
    return response


@router.delete("/ui/api/rbac/accounts/{account_id}", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def delete_rbac_account(request: Request, account_id: str) -> dict[str, bool]:
    request_start = perf_counter()
    db = db_or_503(request)
    existing = await db.query_raw(
        """
        SELECT account_id, email, role, is_active
        FROM deltallm_platformaccount
        WHERE account_id = $1
        LIMIT 1
        """,
        account_id,
    )
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    # Manual delete order keeps behavior deterministic regardless of FK cascade configuration.
    await db.execute_raw("DELETE FROM deltallm_teammembership WHERE account_id = $1", account_id)
    await db.execute_raw("DELETE FROM deltallm_organizationmembership WHERE account_id = $1", account_id)
    await db.execute_raw("DELETE FROM deltallm_platformsession WHERE account_id = $1", account_id)
    await db.execute_raw("DELETE FROM deltallm_platformidentity WHERE account_id = $1", account_id)
    deleted = await db.execute_raw("DELETE FROM deltallm_platformaccount WHERE account_id = $1", account_id)
    response = {"deleted": int(deleted or 0) > 0}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_RBAC_ACCOUNT_DELETE,
        resource_type="platform_account",
        resource_id=account_id,
        response_payload=response,
        before=to_json_value(dict(existing[0])),
    )
    return response


@router.get("/ui/api/rbac/organization-memberships", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def list_org_memberships(request: Request, account_id: str | None = None) -> list[dict[str, Any]]:
    db = db_or_503(request)
    if account_id:
        rows = await db.query_raw(
            """
            SELECT membership_id, account_id, organization_id, role, created_at, updated_at
            FROM deltallm_organizationmembership
            WHERE account_id = $1
            ORDER BY created_at DESC
            """,
            account_id,
        )
    else:
        rows = await db.query_raw(
            """
            SELECT membership_id, account_id, organization_id, role, created_at, updated_at
            FROM deltallm_organizationmembership
            ORDER BY created_at DESC
            """
        )
    return [to_json_value(dict(row)) for row in rows]


@router.post("/ui/api/rbac/organization-memberships", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def upsert_org_membership(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
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
        rows = await db.query_raw("SELECT account_id FROM deltallm_platformaccount WHERE lower(email)=lower($1) LIMIT 1", email)
        if rows:
            account_id = rows[0].get("account_id")
    if not account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="account_id or known email is required")

    await db.execute_raw(
        """
        INSERT INTO deltallm_organizationmembership (membership_id, account_id, organization_id, role, created_at, updated_at)
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
        FROM deltallm_organizationmembership
        WHERE account_id = $1 AND organization_id = $2
        LIMIT 1
        """,
        account_id,
        organization_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="membership upsert failed")
    response = to_json_value(dict(rows[0]))
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_RBAC_ORG_MEMBERSHIP_UPSERT,
        resource_type="organization_membership",
        resource_id=str(rows[0].get("membership_id") or ""),
        request_payload=payload,
        response_payload=response if isinstance(response, dict) else None,
    )
    return response


@router.delete("/ui/api/rbac/organization-memberships/{membership_id}", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def delete_org_membership(request: Request, membership_id: str) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    existing_rows = await db.query_raw(
        """
        SELECT membership_id, account_id, organization_id, role, created_at, updated_at
        FROM deltallm_organizationmembership
        WHERE membership_id = $1
        LIMIT 1
        """,
        membership_id,
    )
    if not existing_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization membership not found")

    existing = dict(existing_rows[0])
    account_id = str(existing.get("account_id") or "")
    organization_id = str(existing.get("organization_id") or "")
    removed_team_memberships = await db.execute_raw(
        """
        DELETE FROM deltallm_teammembership
        WHERE account_id = $1
          AND team_id IN (
            SELECT team_id
            FROM deltallm_teamtable
            WHERE organization_id = $2
          )
        """,
        account_id,
        organization_id,
    )

    deleted = await db.execute_raw(
        "DELETE FROM deltallm_organizationmembership WHERE membership_id = $1",
        membership_id,
    )
    response = {
        "deleted": int(deleted or 0) > 0,
        "team_memberships_removed": int(removed_team_memberships or 0),
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_RBAC_ORG_MEMBERSHIP_DELETE,
        resource_type="organization_membership",
        resource_id=membership_id,
        response_payload=response,
        before=to_json_value(existing),
    )
    return response


@router.get("/ui/api/rbac/team-memberships", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def list_team_memberships(request: Request, account_id: str | None = None) -> list[dict[str, Any]]:
    db = db_or_503(request)
    if account_id:
        rows = await db.query_raw(
            """
            SELECT membership_id, account_id, team_id, role, created_at, updated_at
            FROM deltallm_teammembership
            WHERE account_id = $1
            ORDER BY created_at DESC
            """,
            account_id,
        )
    else:
        rows = await db.query_raw(
            """
            SELECT membership_id, account_id, team_id, role, created_at, updated_at
            FROM deltallm_teammembership
            ORDER BY created_at DESC
            """
        )
    return [to_json_value(dict(row)) for row in rows]


@router.post("/ui/api/rbac/team-memberships", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def upsert_team_membership(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
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
        rows = await db.query_raw("SELECT account_id FROM deltallm_platformaccount WHERE lower(email)=lower($1) LIMIT 1", email)
        if rows:
            account_id = rows[0].get("account_id")
    if not account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="account_id or known email is required")

    account_rows = await db.query_raw(
        "SELECT account_id FROM deltallm_platformaccount WHERE account_id = $1 LIMIT 1",
        account_id,
    )
    if not account_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    team_rows = await db.query_raw(
        "SELECT team_id, organization_id FROM deltallm_teamtable WHERE team_id = $1 LIMIT 1",
        team_id,
    )
    if not team_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    organization_id = team_rows[0].get("organization_id")
    if organization_id:
        org_membership_rows = await db.query_raw(
            """
            SELECT membership_id
            FROM deltallm_organizationmembership
            WHERE account_id = $1 AND organization_id = $2
            LIMIT 1
            """,
            account_id,
            organization_id,
        )
        if not org_membership_rows:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account must be a member of the team's organization")

    await db.execute_raw(
        """
        INSERT INTO deltallm_teammembership (membership_id, account_id, team_id, role, created_at, updated_at)
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
        FROM deltallm_teammembership
        WHERE account_id = $1 AND team_id = $2
        LIMIT 1
        """,
        account_id,
        team_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="membership upsert failed")
    response = to_json_value(dict(rows[0]))
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_RBAC_TEAM_MEMBERSHIP_UPSERT,
        resource_type="team_membership",
        resource_id=str(rows[0].get("membership_id") or ""),
        request_payload=payload,
        response_payload=response if isinstance(response, dict) else None,
    )
    return response


@router.delete("/ui/api/rbac/team-memberships/{membership_id}", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def delete_team_membership(request: Request, membership_id: str) -> dict[str, bool]:
    request_start = perf_counter()
    db = db_or_503(request)
    deleted = await db.execute_raw(
        "DELETE FROM deltallm_teammembership WHERE membership_id = $1",
        membership_id,
    )
    response = {"deleted": int(deleted or 0) > 0}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_RBAC_TEAM_MEMBERSHIP_DELETE,
        resource_type="team_membership",
        resource_id=membership_id,
        response_payload=response,
    )
    return response
