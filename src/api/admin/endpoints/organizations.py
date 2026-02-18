from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.auth.roles import Permission
from src.api.admin.endpoints.common import db_or_503, optional_int, to_json_value
from src.middleware.admin import require_admin_permission

router = APIRouter(tags=["Admin Organizations"])


@router.get("/ui/api/organizations", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def list_organizations(request: Request) -> list[dict[str, Any]]:
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT organization_id, organization_name, max_budget, spend, rpm_limit, tpm_limit, created_at, updated_at
        FROM litellm_organizationtable
        ORDER BY created_at DESC
        """
    )
    return [to_json_value(dict(row)) for row in rows]


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
        INSERT INTO litellm_organizationtable (
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


@router.put("/ui/api/organizations/{organization_id}", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def update_organization(request: Request, organization_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT organization_id, organization_name, max_budget, spend, rpm_limit, tpm_limit, created_at, updated_at
        FROM litellm_organizationtable
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
        UPDATE litellm_organizationtable
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
        FROM litellm_organizationtable
        WHERE organization_id = $1
        LIMIT 1
        """,
        organization_id,
    )
    if not updated_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return to_json_value(dict(updated_rows[0]))
