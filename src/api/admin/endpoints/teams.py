from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.api.admin.endpoints.common import db_or_503, optional_int, to_json_value
from src.middleware.admin import require_master_key

router = APIRouter(tags=["Admin Teams"])


@router.get("/ui/api/teams", dependencies=[Depends(require_master_key)])
async def list_teams(request: Request) -> list[dict[str, Any]]:
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT t.team_id, t.team_alias, t.organization_id, t.max_budget, t.spend, t.models, t.rpm_limit, t.tpm_limit, t.blocked,
               t.created_at, t.updated_at,
               (SELECT COUNT(*) FROM litellm_usertable u WHERE u.team_id = t.team_id) AS member_count
        FROM litellm_teamtable t
        ORDER BY t.created_at DESC
        """
    )
    return [to_json_value(dict(row)) for row in rows]


@router.post("/ui/api/teams", dependencies=[Depends(require_master_key)])
async def create_team(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    db = db_or_503(request)
    team_id = str(payload.get("team_id") or f"team-{secrets.token_hex(6)}")
    team_alias = payload.get("team_alias")
    organization_id = payload.get("organization_id")
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


@router.put("/ui/api/teams/{team_id}", dependencies=[Depends(require_master_key)])
async def update_team(request: Request, team_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    db = db_or_503(request)
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

    existing = dict(rows[0])
    team_alias = payload.get("team_alias", existing.get("team_alias"))
    organization_id = payload.get("organization_id", existing.get("organization_id"))
    max_budget = payload.get("max_budget", existing.get("max_budget"))
    rpm_limit = optional_int(payload.get("rpm_limit", existing.get("rpm_limit")), "rpm_limit")
    tpm_limit = optional_int(payload.get("tpm_limit", existing.get("tpm_limit")), "tpm_limit")
    models = payload.get("models", existing.get("models"))
    if not isinstance(models, list):
        models = existing.get("models") or []

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


@router.get("/ui/api/teams/{team_id}/members", dependencies=[Depends(require_master_key)])
async def list_team_members(request: Request, team_id: str) -> list[dict[str, Any]]:
    db = db_or_503(request)
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


@router.post("/ui/api/teams/{team_id}/members", dependencies=[Depends(require_master_key)])
async def add_team_member(request: Request, team_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    db = db_or_503(request)
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


@router.delete("/ui/api/teams/{team_id}/members/{user_id}", dependencies=[Depends(require_master_key)])
async def remove_team_member(request: Request, team_id: str, user_id: str) -> dict[str, bool]:
    db = db_or_503(request)
    updated = await db.execute_raw(
        "UPDATE litellm_usertable SET team_id = NULL, updated_at = NOW() WHERE team_id = $1 AND user_id = $2",
        team_id,
        user_id,
    )
    return {"removed": int(updated or 0) > 0}
