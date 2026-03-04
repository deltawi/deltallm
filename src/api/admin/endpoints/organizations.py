from __future__ import annotations

import secrets
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from src.auth.roles import OrganizationRole, Permission
from src.audit.actions import AuditAction
from src.api.admin.endpoints.common import db_or_503, emit_admin_mutation_audit, optional_int, to_json_value, get_auth_scope, AuthScope
from src.db.repositories import AUDIT_METADATA_RETENTION_DAYS_KEY, AUDIT_PAYLOAD_RETENTION_DAYS_KEY
from src.middleware.admin import require_admin_permission

router = APIRouter(tags=["Admin Organizations"])


def _audit_retention_metadata(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any] | None:
    metadata: dict[str, Any] = {}
    raw_metadata = payload.get("metadata")
    if isinstance(raw_metadata, dict):
        metadata.update(raw_metadata)
    if isinstance(existing, dict):
        metadata = {**existing, **metadata}

    metadata_changed = False
    for field_name in (AUDIT_METADATA_RETENTION_DAYS_KEY, AUDIT_PAYLOAD_RETENTION_DAYS_KEY):
        if field_name not in payload:
            continue
        value = optional_int(payload.get(field_name), field_name)
        if value is None:
            metadata.pop(field_name, None)
            metadata_changed = True
            continue
        if value < 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be >= 1")
        metadata[field_name] = value
        metadata_changed = True

    if not metadata and not metadata_changed:
        return None
    return metadata


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

    select_cols = """o.organization_id, o.organization_name, o.max_budget, o.spend, o.rpm_limit, o.tpm_limit,
                   o.audit_content_storage_enabled, o.metadata, o.created_at, o.updated_at,
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
        SELECT organization_id, organization_name, max_budget, spend, rpm_limit, tpm_limit, audit_content_storage_enabled, metadata, created_at, updated_at
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
    request_start = perf_counter()
    db = db_or_503(request)
    organization_id = str(payload.get("organization_id") or f"org-{secrets.token_hex(6)}")
    organization_name = payload.get("organization_name")
    max_budget = payload.get("max_budget")
    rpm_limit = optional_int(payload.get("rpm_limit"), "rpm_limit")
    tpm_limit = optional_int(payload.get("tpm_limit"), "tpm_limit")
    metadata = _audit_retention_metadata(payload)

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
            metadata,
            created_at,
            updated_at
        )
        VALUES (gen_random_uuid(), $1, $2, $3, 0, $4, $5, $6::jsonb, NOW(), NOW())
        ON CONFLICT (organization_id)
        DO UPDATE SET
            organization_name = EXCLUDED.organization_name,
            max_budget = EXCLUDED.max_budget,
            rpm_limit = EXCLUDED.rpm_limit,
            tpm_limit = EXCLUDED.tpm_limit,
            metadata = COALESCE(EXCLUDED.metadata, deltallm_organizationtable.metadata),
            updated_at = NOW()
        """,
        organization_id,
        organization_name,
        max_budget,
        rpm_limit,
        tpm_limit,
        metadata if metadata is not None else None,
    )
    response = {
        "organization_id": organization_id,
        "organization_name": organization_name,
        "max_budget": max_budget,
        "rpm_limit": rpm_limit,
        "tpm_limit": tpm_limit,
        "metadata": metadata or {},
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ORGANIZATION_CREATE,
        resource_type="organization",
        resource_id=organization_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.put("/ui/api/organizations/{organization_id}", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def update_organization(request: Request, organization_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT organization_id, organization_name, max_budget, spend, rpm_limit, tpm_limit, audit_content_storage_enabled, metadata, created_at, updated_at
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
    metadata = _audit_retention_metadata(payload, existing.get("metadata") if isinstance(existing.get("metadata"), dict) else None)

    await db.execute_raw(
        """
        UPDATE deltallm_organizationtable
        SET organization_name = $1,
            max_budget = $2,
            rpm_limit = $3,
            tpm_limit = $4,
            metadata = COALESCE($5::jsonb, metadata),
            updated_at = NOW()
        WHERE organization_id = $6
        """,
        organization_name,
        max_budget,
        rpm_limit,
        tpm_limit,
        metadata if metadata is not None else None,
        organization_id,
    )
    updated_rows = await db.query_raw(
        """
        SELECT organization_id, organization_name, max_budget, spend, rpm_limit, tpm_limit, audit_content_storage_enabled, metadata, created_at, updated_at
        FROM deltallm_organizationtable
        WHERE organization_id = $1
        LIMIT 1
        """,
        organization_id,
    )
    if not updated_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    updated = to_json_value(dict(updated_rows[0]))
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ORGANIZATION_UPDATE,
        resource_type="organization",
        resource_id=organization_id,
        request_payload=payload,
        response_payload=updated if isinstance(updated, dict) else None,
        before=to_json_value(existing),
        after=updated if isinstance(updated, dict) else None,
    )
    return updated


@router.get("/ui/api/organizations/{organization_id}/members", dependencies=[Depends(require_admin_permission(Permission.ORG_READ))])
async def list_organization_members(request: Request, organization_id: str) -> list[dict[str, Any]]:
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT
            om.membership_id,
            om.account_id,
            pa.email,
            om.role AS org_role,
            om.created_at,
            om.updated_at,
            COALESCE(team_stats.team_count, 0) AS team_count,
            COALESCE(team_stats.teams, ARRAY[]::text[]) AS teams
        FROM deltallm_organizationmembership om
        JOIN deltallm_platformaccount pa
          ON pa.account_id = om.account_id
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*)::int AS team_count,
                ARRAY_AGG(COALESCE(t.team_alias, t.team_id) ORDER BY t.team_alias, t.team_id) AS teams
            FROM deltallm_teammembership tm
            JOIN deltallm_teamtable t
              ON t.team_id = tm.team_id
            WHERE tm.account_id = om.account_id
              AND t.organization_id = $1
        ) team_stats ON true
        WHERE om.organization_id = $1
        ORDER BY om.created_at DESC
        """,
        organization_id,
    )
    return [to_json_value(dict(row)) for row in rows]


@router.get("/ui/api/organizations/{organization_id}/member-candidates", dependencies=[Depends(require_admin_permission(Permission.ORG_READ))])
async def list_organization_member_candidates(
    request: Request,
    organization_id: str,
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> list[dict[str, Any]]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.ORG_READ)
    db = db_or_503(request)
    if not scope.is_platform_admin and organization_id not in scope.org_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    # Privacy-by-default: do not return broad account listings.
    # Callers must provide an exact user identifier (case-insensitive).
    normalized_search = (search or "").strip()
    if not normalized_search:
        return []

    clauses: list[str] = []
    params: list[Any] = []

    params.append(normalized_search)
    # Exact (case-insensitive) matching on either email or account_id.
    clauses.append(f"(lower(email) = lower(${len(params)}) OR lower(account_id::text) = lower(${len(params)}))")
    if not scope.is_platform_admin:
        params.append(organization_id)
        clauses.append(
            "("
            f"EXISTS (SELECT 1 FROM deltallm_organizationmembership om WHERE om.account_id = deltallm_platformaccount.account_id AND om.organization_id = ${len(params)})"
            " OR "
            "NOT EXISTS (SELECT 1 FROM deltallm_organizationmembership om_any WHERE om_any.account_id = deltallm_platformaccount.account_id)"
            ")"
        )

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = await db.query_raw(
        f"""
        SELECT account_id, email, role, is_active, created_at, updated_at
        FROM deltallm_platformaccount
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ${len(params)}
        """,
        *params,
    )
    return [to_json_value(dict(row)) for row in rows]


@router.post("/ui/api/organizations/{organization_id}/members", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def add_organization_member(request: Request, organization_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    account_id = payload.get("account_id")
    email = str(payload.get("email") or "").strip().lower()
    role = str(payload.get("role") or OrganizationRole.MEMBER)
    allowed_roles = {
        OrganizationRole.MEMBER,
        OrganizationRole.OWNER,
        OrganizationRole.ADMIN,
        OrganizationRole.BILLING,
        OrganizationRole.AUDITOR,
    }
    if role not in allowed_roles:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid organization role")

    org_rows = await db.query_raw(
        "SELECT organization_id FROM deltallm_organizationtable WHERE organization_id = $1 LIMIT 1",
        organization_id,
    )
    if not org_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    if not account_id and email:
        rows = await db.query_raw(
            "SELECT account_id FROM deltallm_platformaccount WHERE lower(email)=lower($1) LIMIT 1",
            email,
        )
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


@router.delete("/ui/api/organizations/{organization_id}/members/{membership_id}", dependencies=[Depends(require_admin_permission(Permission.ORG_UPDATE))])
async def remove_organization_member(request: Request, organization_id: str, membership_id: str) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT membership_id, account_id
        FROM deltallm_organizationmembership
        WHERE membership_id = $1 AND organization_id = $2
        LIMIT 1
        """,
        membership_id,
        organization_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization membership not found")
    account_id = rows[0].get("account_id")
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
    )
    return response


@router.get("/ui/api/organizations/{organization_id}/teams", dependencies=[Depends(require_admin_permission(Permission.ORG_READ))])
async def list_organization_teams(request: Request, organization_id: str) -> list[dict[str, Any]]:
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT t.team_id, t.team_alias, t.max_budget, t.spend, t.rpm_limit, t.tpm_limit, t.models, t.blocked, t.created_at, t.updated_at,
               (SELECT COUNT(*) FROM deltallm_teammembership tm WHERE tm.team_id = t.team_id) AS member_count
        FROM deltallm_teamtable t
        WHERE t.organization_id = $1
        ORDER BY t.created_at DESC
        """,
        organization_id,
    )
    return [to_json_value(dict(row)) for row in rows]
