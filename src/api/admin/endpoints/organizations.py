from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from src.auth.roles import Permission
from src.api.admin.endpoints.common import db_or_503, optional_int, to_json_value, get_auth_scope, AuthScope
from src.middleware.admin import require_admin_permission

router = APIRouter(tags=["Admin Organizations"])


@router.get("/ui/api/organizations")
async def list_organizations(
    request: Request,
    search: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_READ)
    db = db_or_503(request)

    clauses: list[str] = []
    params: list[Any] = []

    if not scope.is_platform_admin:
        if scope.org_ids:
            ph = ", ".join(f"${len(params) + i + 1}" for i in range(len(scope.org_ids)))
            params.extend(scope.org_ids)
            clauses.append(f"o.organization_id IN ({ph})")
        else:
            return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}

    if search:
        params.append(f"%{search}%")
        clauses.append(f"(o.organization_name ILIKE ${len(params)} OR o.organization_id ILIKE ${len(params)})")

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    select_cols = """o.organization_id, o.organization_name, o.max_budget, o.spend, o.rpm_limit, o.tpm_limit, o.created_at, o.updated_at,
                   (SELECT COUNT(*) FROM deltallm_teamtable t WHERE t.organization_id = o.organization_id) AS team_count"""

    count_rows = await db.query_raw(
        f"SELECT COUNT(*) AS total FROM deltallm_organizationtable o {where_sql}",
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)

    params.append(limit)
    params.append(offset)
    rows = await db.query_raw(
        f"""
        SELECT {select_cols}
        FROM deltallm_organizationtable o
        {where_sql}
        ORDER BY o.created_at DESC
        LIMIT ${len(params) - 1} OFFSET ${len(params)}
        """,
        *params,
    )

    return {
        "data": [to_json_value(dict(row)) for row in rows],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.get("/ui/api/organizations/{organization_id}", dependencies=[Depends(require_admin_permission(Permission.ORG_READ))])
async def get_organization(request: Request, organization_id: str) -> dict[str, Any]:
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT organization_id, organization_name, max_budget, spend, rpm_limit, tpm_limit, created_at, updated_at
        FROM deltallm_organizationtable
        WHERE organization_id = $1
        LIMIT 1
        """,
        organization_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return to_json_value(dict(rows[0]))


@router.post("/ui/api/organizations", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def create_organization(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    db = db_or_503(request)
    organization_id = str(payload.get("organization_id") or f"org-{secrets.token_hex(6)}")
    organization_name = payload.get("organization_name")
    max_budget = payload.get("max_budget")
    rpm_limit = optional_int(payload.get("rpm_limit"), "rpm_limit")
    tpm_limit = optional_int(payload.get("tpm_limit"), "tpm_limit")

    await db.execute_raw(
        """
        INSERT INTO deltallm_organizationtable (
            id,
            organization_id,
            organization_name,
            max_budget,
            spend,
            rpm_limit,
            tpm_limit,
            created_at,
            updated_at
        )
        VALUES (gen_random_uuid(), $1, $2, $3, 0, $4, $5, NOW(), NOW())
        ON CONFLICT (organization_id)
        DO UPDATE SET
            organization_name = EXCLUDED.organization_name,
            max_budget = EXCLUDED.max_budget,
            rpm_limit = EXCLUDED.rpm_limit,
            tpm_limit = EXCLUDED.tpm_limit,
            updated_at = NOW()
        """,
        organization_id,
        organization_name,
        max_budget,
        rpm_limit,
        tpm_limit,
    )
    return {
        "organization_id": organization_id,
        "organization_name": organization_name,
        "max_budget": max_budget,
        "rpm_limit": rpm_limit,
        "tpm_limit": tpm_limit,
    }


@router.put("/ui/api/organizations/{organization_id}", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def update_organization(request: Request, organization_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT organization_id, organization_name, max_budget, spend, rpm_limit, tpm_limit, created_at, updated_at
        FROM deltallm_organizationtable
        WHERE organization_id = $1
        LIMIT 1
        """,
        organization_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    existing = dict(rows[0])
    organization_name = payload.get("organization_name", existing.get("organization_name"))
    max_budget = payload.get("max_budget", existing.get("max_budget"))
    rpm_limit = optional_int(payload.get("rpm_limit", existing.get("rpm_limit")), "rpm_limit")
    tpm_limit = optional_int(payload.get("tpm_limit", existing.get("tpm_limit")), "tpm_limit")

    await db.execute_raw(
        """
        UPDATE deltallm_organizationtable
        SET organization_name = $1,
            max_budget = $2,
            rpm_limit = $3,
            tpm_limit = $4,
            updated_at = NOW()
        WHERE organization_id = $5
        """,
        organization_name,
        max_budget,
        rpm_limit,
        tpm_limit,
        organization_id,
    )
    updated_rows = await db.query_raw(
        """
        SELECT organization_id, organization_name, max_budget, spend, rpm_limit, tpm_limit, created_at, updated_at
        FROM deltallm_organizationtable
        WHERE organization_id = $1
        LIMIT 1
        """,
        organization_id,
    )
    if not updated_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return to_json_value(dict(updated_rows[0]))


@router.get("/ui/api/organizations/{organization_id}/members", dependencies=[Depends(require_admin_permission(Permission.ORG_READ))])
async def list_organization_members(request: Request, organization_id: str) -> list[dict[str, Any]]:
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT u.user_id, u.user_email, u.user_role, u.spend, u.max_budget, u.team_id, t.team_alias, u.created_at, u.updated_at
        FROM deltallm_usertable u
        LEFT JOIN deltallm_teamtable t ON u.team_id = t.team_id
        WHERE t.organization_id = $1
        ORDER BY u.created_at DESC
        """,
        organization_id,
    )
    return [to_json_value(dict(row)) for row in rows]


@router.get("/ui/api/organizations/{organization_id}/teams", dependencies=[Depends(require_admin_permission(Permission.ORG_READ))])
async def list_organization_teams(request: Request, organization_id: str) -> list[dict[str, Any]]:
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT t.team_id, t.team_alias, t.max_budget, t.spend, t.rpm_limit, t.tpm_limit, t.models, t.blocked, t.created_at, t.updated_at,
               (SELECT COUNT(*) FROM deltallm_usertable u WHERE u.team_id = t.team_id) AS member_count
        FROM deltallm_teamtable t
        WHERE t.organization_id = $1
        ORDER BY t.created_at DESC
        """,
        organization_id,
    )
    return [to_json_value(dict(row)) for row in rows]
