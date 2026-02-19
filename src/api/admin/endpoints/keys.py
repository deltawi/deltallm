from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from src.auth.roles import Permission
from src.api.admin.endpoints.common import db_or_503, to_json_value, get_auth_scope
from src.middleware.admin import require_admin_permission

router = APIRouter(tags=["Admin Keys"])


@router.get("/ui/api/keys")
async def list_keys(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> list[dict[str, Any]]:
    scope = get_auth_scope(request, authorization, x_master_key)
    db = db_or_503(request)

    if scope.is_platform_admin:
        rows = await db.query_raw(
            """
            SELECT token, key_name, user_id, team_id, models, spend, max_budget, rpm_limit, tpm_limit, expires, created_at, updated_at
            FROM litellm_verificationtoken
            ORDER BY created_at DESC
            """
        )
    elif scope.org_ids:
        placeholders = ", ".join(f"${i+1}" for i in range(len(scope.org_ids)))
        rows = await db.query_raw(
            f"""
            SELECT vt.token, vt.key_name, vt.user_id, vt.team_id, vt.models, vt.spend, vt.max_budget, vt.rpm_limit, vt.tpm_limit, vt.expires, vt.created_at, vt.updated_at
            FROM litellm_verificationtoken vt
            LEFT JOIN litellm_teamtable t ON vt.team_id = t.team_id
            WHERE t.organization_id IN ({placeholders})
            ORDER BY vt.created_at DESC
            """,
            *scope.org_ids,
        )
    else:
        rows = []

    return [to_json_value(dict(row)) for row in rows]


@router.post("/ui/api/keys", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def create_key(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    db = db_or_503(request)
    raw_key = f"sk-{secrets.token_urlsafe(24)}"
    key_service = request.app.state.key_service
    token_hash = key_service.hash_key(raw_key)

    key_name = payload.get("key_name")
    user_id = payload.get("user_id")
    team_id = payload.get("team_id")
    models = payload.get("models") if isinstance(payload.get("models"), list) else []
    max_budget = payload.get("max_budget")
    rpm_limit = payload.get("rpm_limit")
    tpm_limit = payload.get("tpm_limit")
    expires = payload.get("expires")
    if expires is not None and not isinstance(expires, str):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="expires must be a string datetime")

    await db.execute_raw(
        """
        INSERT INTO litellm_verificationtoken (id, token, key_name, user_id, team_id, models, spend, max_budget, rpm_limit, tpm_limit, expires, created_at, updated_at)
        VALUES (gen_random_uuid(), $1, $2, $3, $4, $5::text[], 0, $6, $7, $8, $9::timestamp, NOW(), NOW())
        """,
        token_hash,
        key_name,
        user_id,
        team_id,
        models,
        max_budget,
        rpm_limit,
        tpm_limit,
        expires,
    )

    return {
        "token": token_hash,
        "raw_key": raw_key,
        "key_name": key_name,
        "user_id": user_id,
        "team_id": team_id,
        "models": models,
        "max_budget": max_budget,
        "rpm_limit": rpm_limit,
        "tpm_limit": tpm_limit,
        "expires": expires,
    }


@router.put("/ui/api/keys/{token_hash}", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def update_key(request: Request, token_hash: str, payload: dict[str, Any]) -> dict[str, Any]:
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT token, key_name, user_id, team_id, models, spend, max_budget, rpm_limit, tpm_limit, expires, created_at, updated_at
        FROM litellm_verificationtoken
        WHERE token = $1
        LIMIT 1
        """,
        token_hash,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")

    existing = dict(rows[0])
    models = payload.get("models", existing.get("models"))
    if not isinstance(models, list):
        models = existing.get("models") or []

    expires = payload.get("expires", existing.get("expires"))
    if expires is not None and not isinstance(expires, str):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="expires must be a string datetime")

    key_name = payload.get("key_name", existing.get("key_name"))
    user_id = payload.get("user_id", existing.get("user_id"))
    team_id = payload.get("team_id", existing.get("team_id"))
    max_budget = payload.get("max_budget", existing.get("max_budget"))
    rpm_limit = payload.get("rpm_limit", existing.get("rpm_limit"))
    tpm_limit = payload.get("tpm_limit", existing.get("tpm_limit"))

    await db.execute_raw(
        """
        UPDATE litellm_verificationtoken
        SET key_name = $1,
            user_id = $2,
            team_id = $3,
            models = $4::text[],
            max_budget = $5,
            rpm_limit = $6,
            tpm_limit = $7,
            expires = $8::timestamp,
            updated_at = NOW()
        WHERE token = $9
        """,
        key_name,
        user_id,
        team_id,
        models,
        max_budget,
        rpm_limit,
        tpm_limit,
        expires,
        token_hash,
    )

    updated_rows = await db.query_raw(
        """
        SELECT token, key_name, user_id, team_id, models, spend, max_budget, rpm_limit, tpm_limit, expires, created_at, updated_at
        FROM litellm_verificationtoken
        WHERE token = $1
        LIMIT 1
        """,
        token_hash,
    )
    if not updated_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    return to_json_value(dict(updated_rows[0]))


@router.post("/ui/api/keys/{token_hash}/regenerate", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def regenerate_key(request: Request, token_hash: str) -> dict[str, Any]:
    db = db_or_503(request)
    rows = await db.query_raw("SELECT token FROM litellm_verificationtoken WHERE token = $1 LIMIT 1", token_hash)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")

    raw_key = f"sk-{secrets.token_urlsafe(24)}"
    new_hash = request.app.state.key_service.hash_key(raw_key)
    await db.execute_raw(
        "UPDATE litellm_verificationtoken SET token = $1, updated_at = NOW() WHERE token = $2",
        new_hash,
        token_hash,
    )
    return {"token": new_hash, "raw_key": raw_key}


@router.post("/ui/api/keys/{token_hash}/revoke", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def revoke_key(request: Request, token_hash: str) -> dict[str, bool]:
    db = db_or_503(request)
    deleted = await db.execute_raw("DELETE FROM litellm_verificationtoken WHERE token = $1", token_hash)
    return {"revoked": int(deleted or 0) > 0}


@router.delete("/ui/api/keys/{token_hash}", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def delete_key(request: Request, token_hash: str) -> dict[str, bool]:
    db = db_or_503(request)
    deleted = await db.execute_raw("DELETE FROM litellm_verificationtoken WHERE token = $1", token_hash)
    return {"deleted": int(deleted or 0) > 0}
