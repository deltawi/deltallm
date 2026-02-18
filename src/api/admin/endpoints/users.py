from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.api.admin.endpoints.common import db_or_503, optional_int, to_json_value
from src.middleware.admin import require_master_key

router = APIRouter(tags=["Admin Users"])


@router.get("/ui/api/users", dependencies=[Depends(require_master_key)])
async def list_users(request: Request, team_id: str | None = Query(default=None)) -> list[dict[str, Any]]:
    db = db_or_503(request)
    if team_id:
        rows = await db.query_raw(
            """
            SELECT user_id, user_email, user_role, team_id, spend, max_budget, models, tpm_limit, rpm_limit,
                   COALESCE((metadata->>'blocked')::boolean, false) AS blocked, created_at, updated_at
            FROM litellm_usertable
            WHERE team_id = $1
            ORDER BY created_at DESC
            """,
            team_id,
        )
    else:
        rows = await db.query_raw(
            """
            SELECT user_id, user_email, user_role, team_id, spend, max_budget, models, tpm_limit, rpm_limit,
                   COALESCE((metadata->>'blocked')::boolean, false) AS blocked, created_at, updated_at
            FROM litellm_usertable
            ORDER BY created_at DESC
            """
        )
    return [to_json_value(dict(row)) for row in rows]


@router.post("/ui/api/users", dependencies=[Depends(require_master_key)])
async def create_user(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    db = db_or_503(request)
    user_id = str(payload.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id is required")

    user_email = payload.get("user_email")
    user_role = payload.get("user_role") or "user"
    team_id = payload.get("team_id")
    max_budget = payload.get("max_budget")
    rpm_limit = optional_int(payload.get("rpm_limit"), "rpm_limit")
    tpm_limit = optional_int(payload.get("tpm_limit"), "tpm_limit")
    models = payload.get("models") if isinstance(payload.get("models"), list) else []

    await db.execute_raw(
        """
        INSERT INTO litellm_usertable (
            user_id,
            user_email,
            user_role,
            spend,
            models,
            team_id,
            max_budget,
            rpm_limit,
            tpm_limit,
            metadata,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, 0, $4::text[], $5, $6, $7, $8, '{}'::jsonb, NOW(), NOW())
        ON CONFLICT (user_id)
        DO UPDATE SET
            user_email = EXCLUDED.user_email,
            user_role = EXCLUDED.user_role,
            team_id = EXCLUDED.team_id,
            max_budget = EXCLUDED.max_budget,
            models = EXCLUDED.models,
            rpm_limit = EXCLUDED.rpm_limit,
            tpm_limit = EXCLUDED.tpm_limit,
            updated_at = NOW()
        """,
        user_id,
        user_email,
        user_role,
        models,
        team_id,
        max_budget,
        rpm_limit,
        tpm_limit,
    )

    return {
        "user_id": user_id,
        "user_email": user_email,
        "user_role": user_role,
        "team_id": team_id,
        "max_budget": max_budget,
        "rpm_limit": rpm_limit,
        "tpm_limit": tpm_limit,
        "models": models,
        "blocked": False,
    }


@router.put("/ui/api/users/{user_id}", dependencies=[Depends(require_master_key)])
async def update_user(request: Request, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT user_id, user_email, user_role, team_id, spend, max_budget, models, tpm_limit, rpm_limit,
               COALESCE((metadata->>'blocked')::boolean, false) AS blocked, created_at, updated_at
        FROM litellm_usertable
        WHERE user_id = $1
        LIMIT 1
        """,
        user_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    existing = dict(rows[0])
    user_email = payload.get("user_email", existing.get("user_email"))
    user_role = payload.get("user_role", existing.get("user_role"))
    team_id = payload.get("team_id", existing.get("team_id"))
    max_budget = payload.get("max_budget", existing.get("max_budget"))
    models = payload.get("models", existing.get("models"))
    if not isinstance(models, list):
        models = existing.get("models") or []
    rpm_limit = optional_int(payload.get("rpm_limit", existing.get("rpm_limit")), "rpm_limit")
    tpm_limit = optional_int(payload.get("tpm_limit", existing.get("tpm_limit")), "tpm_limit")

    await db.execute_raw(
        """
        UPDATE litellm_usertable
        SET user_email = $1,
            user_role = $2,
            team_id = $3,
            max_budget = $4,
            models = $5::text[],
            rpm_limit = $6,
            tpm_limit = $7,
            updated_at = NOW()
        WHERE user_id = $8
        """,
        user_email,
        user_role,
        team_id,
        max_budget,
        models,
        rpm_limit,
        tpm_limit,
        user_id,
    )
    updated_rows = await db.query_raw(
        """
        SELECT user_id, user_email, user_role, team_id, spend, max_budget, models, tpm_limit, rpm_limit,
               COALESCE((metadata->>'blocked')::boolean, false) AS blocked, created_at, updated_at
        FROM litellm_usertable
        WHERE user_id = $1
        LIMIT 1
        """,
        user_id,
    )
    if not updated_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return to_json_value(dict(updated_rows[0]))


@router.post("/ui/api/users/{user_id}/block", dependencies=[Depends(require_master_key)])
async def block_user(request: Request, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    db = db_or_503(request)
    blocked = bool(payload.get("blocked", True))

    updated = await db.execute_raw(
        """
        UPDATE litellm_usertable
        SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{blocked}', to_jsonb($1::boolean)),
            updated_at = NOW()
        WHERE user_id = $2
        """,
        blocked,
        user_id,
    )
    if int(updated or 0) <= 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return {"user_id": user_id, "blocked": blocked}
