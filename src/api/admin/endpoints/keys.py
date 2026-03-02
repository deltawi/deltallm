from __future__ import annotations

import secrets
import logging
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from src.auth.roles import Permission
from src.audit.actions import AuditAction
from src.api.admin.endpoints.common import db_or_503, to_json_value, get_auth_scope, emit_admin_mutation_audit

router = APIRouter(tags=["Admin Keys"])
logger = logging.getLogger(__name__)


@router.get("/ui/api/keys")
async def list_keys(
    request: Request,
    search: str | None = Query(default=None),
    team_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
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
        clauses.append(f"(vt.key_name ILIKE ${len(params)} OR vt.token ILIKE ${len(params)})")
    if team_id:
        params.append(team_id)
        clauses.append(f"vt.team_id = ${len(params)}")

    join_sql = "LEFT JOIN deltallm_teamtable t ON vt.team_id = t.team_id" if not scope.is_platform_admin else ""
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    count_rows = await db.query_raw(
        f"SELECT COUNT(*) AS total FROM deltallm_verificationtoken vt {join_sql} {where_sql}",
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)

    params.append(limit)
    params.append(offset)
    rows = await db.query_raw(
        f"""
        SELECT vt.token, vt.key_name, vt.user_id, vt.team_id, vt.models, vt.spend, vt.max_budget, vt.rpm_limit, vt.tpm_limit, vt.expires, vt.created_at, vt.updated_at
        FROM deltallm_verificationtoken vt {join_sql}
        {where_sql}
        ORDER BY vt.created_at DESC
        LIMIT ${len(params) - 1} OFFSET ${len(params)}
        """,
        *params,
    )

    return {
        "data": [to_json_value(dict(row)) for row in rows],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.post("/ui/api/keys")
async def create_key(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_UPDATE)
    db = db_or_503(request)

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

    if not scope.is_platform_admin:
        if not team_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A team must be selected when creating a key")
        rows = await db.query_raw(
            "SELECT organization_id FROM deltallm_teamtable WHERE team_id = $1 LIMIT 1",
            team_id,
        )
        if not rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
        team_org = rows[0].get("organization_id")
        if not team_org or team_org not in scope.org_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only create keys for teams in your organizations")

    raw_key = f"sk-{secrets.token_urlsafe(24)}"
    key_service = request.app.state.key_service
    token_hash = key_service.hash_key(raw_key)

    try:
        await db.execute_raw(
            """
            INSERT INTO deltallm_verificationtoken (id, token, key_name, user_id, team_id, models, spend, max_budget, rpm_limit, tpm_limit, expires, created_at, updated_at)
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

        response = {
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
        await emit_admin_mutation_audit(
            request=request,
            request_start=request_start,
            action=AuditAction.ADMIN_KEY_CREATE,
            scope=scope,
            resource_type="api_key",
            resource_id=token_hash,
            request_payload=payload,
            response_payload=response,
        )
        return response
    except Exception as exc:
        await emit_admin_mutation_audit(
            request=request,
            request_start=request_start,
            action=AuditAction.ADMIN_KEY_CREATE,
            scope=scope,
            resource_type="api_key",
            resource_id=token_hash,
            request_payload=payload,
            status="error",
            error=exc,
        )
        raise
@router.put("/ui/api/keys/{token_hash}")
async def update_key(
    request: Request,
    token_hash: str,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_UPDATE)
    db = db_or_503(request)
    await _require_key_access(scope, db, token_hash)
    rows = await db.query_raw(
        """
        SELECT token, key_name, user_id, team_id, models, spend, max_budget, rpm_limit, tpm_limit, expires, created_at, updated_at
        FROM deltallm_verificationtoken
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

    try:
        await db.execute_raw(
            """
            UPDATE deltallm_verificationtoken
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
            FROM deltallm_verificationtoken
            WHERE token = $1
            LIMIT 1
            """,
            token_hash,
        )
        if not updated_rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
        updated = to_json_value(dict(updated_rows[0]))
        await emit_admin_mutation_audit(
            request=request,
            request_start=request_start,
            action=AuditAction.ADMIN_KEY_UPDATE,
            scope=scope,
            resource_type="api_key",
            resource_id=token_hash,
            request_payload=payload,
            before=to_json_value(existing),
            after=updated if isinstance(updated, dict) else None,
            response_payload=updated if isinstance(updated, dict) else None,
        )
        return updated
    except Exception as exc:
        await emit_admin_mutation_audit(
            request=request,
            request_start=request_start,
            action=AuditAction.ADMIN_KEY_UPDATE,
            scope=scope,
            resource_type="api_key",
            resource_id=token_hash,
            request_payload=payload,
            status="error",
            error=exc,
        )
        raise


async def _require_key_access(scope, db, token_hash: str) -> None:
    if scope.is_platform_admin:
        return
    rows = await db.query_raw(
        """
        SELECT t.organization_id FROM deltallm_verificationtoken vt
        JOIN deltallm_teamtable t ON vt.team_id = t.team_id
        WHERE vt.token = $1 LIMIT 1
        """,
        token_hash,
    )
    if not rows or not rows[0].get("organization_id") or rows[0]["organization_id"] not in scope.org_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")


@router.post("/ui/api/keys/{token_hash}/regenerate")
async def regenerate_key(
    request: Request,
    token_hash: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_UPDATE)
    db = db_or_503(request)
    await _require_key_access(scope, db, token_hash)

    rows = await db.query_raw("SELECT token FROM deltallm_verificationtoken WHERE token = $1 LIMIT 1", token_hash)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")

    raw_key = f"sk-{secrets.token_urlsafe(24)}"
    new_hash = request.app.state.key_service.hash_key(raw_key)
    await db.execute_raw(
        "UPDATE deltallm_verificationtoken SET token = $1, updated_at = NOW() WHERE token = $2",
        new_hash,
        token_hash,
    )
    try:
        await request.app.state.key_service.invalidate_key_cache_by_hash(token_hash)
        await request.app.state.key_service.invalidate_key_cache_by_hash(new_hash)
    except Exception:
        logger.exception("failed to invalidate key auth cache after regenerate")
    response = {"token": new_hash, "raw_key": raw_key}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_KEY_REGENERATE,
        scope=scope,
        resource_type="api_key",
        resource_id=new_hash,
        request_payload={"previous_token": token_hash},
        response_payload=response,
    )
    return response


@router.post("/ui/api/keys/{token_hash}/revoke")
async def revoke_key(
    request: Request,
    token_hash: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, bool]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_REVOKE)
    db = db_or_503(request)
    await _require_key_access(scope, db, token_hash)
    deleted = await db.execute_raw("DELETE FROM deltallm_verificationtoken WHERE token = $1", token_hash)
    if int(deleted or 0) > 0:
        try:
            await request.app.state.key_service.invalidate_key_cache_by_hash(token_hash)
        except Exception:
            logger.exception("failed to invalidate key auth cache after revoke")
    response = {"revoked": int(deleted or 0) > 0}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_KEY_REVOKE,
        scope=scope,
        resource_type="api_key",
        resource_id=token_hash,
        response_payload=response,
    )
    return response


@router.delete("/ui/api/keys/{token_hash}")
async def delete_key(
    request: Request,
    token_hash: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, bool]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_UPDATE)
    db = db_or_503(request)
    await _require_key_access(scope, db, token_hash)
    deleted = await db.execute_raw("DELETE FROM deltallm_verificationtoken WHERE token = $1", token_hash)
    if int(deleted or 0) > 0:
        try:
            await request.app.state.key_service.invalidate_key_cache_by_hash(token_hash)
        except Exception:
            logger.exception("failed to invalidate key auth cache after delete")
    response = {"deleted": int(deleted or 0) > 0}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_KEY_DELETE,
        scope=scope,
        resource_type="api_key",
        resource_id=token_hash,
        response_payload=response,
    )
    return response
