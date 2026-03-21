from __future__ import annotations

import secrets
import logging
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from src.auth.roles import Permission
from src.audit.actions import AuditAction
from src.api.admin.endpoints.common import (
    db_or_503,
    emit_admin_mutation_audit,
    get_auth_scope,
    to_json_value,
    validate_runtime_user_scope,
)
from src.middleware.platform_auth import get_platform_auth_context
from src.services.asset_visibility_preview import build_asset_visibility_preview
from src.services.scoped_asset_access import apply_scope_asset_access, build_scope_asset_access

router = APIRouter(tags=["Admin Keys"])
logger = logging.getLogger(__name__)


async def _get_self_service_policy(db: Any, team_id: str) -> dict[str, Any]:
    rows = await db.query_raw(
        """
        SELECT self_service_keys_enabled, self_service_max_keys_per_user,
               self_service_budget_ceiling, self_service_require_expiry,
               self_service_max_expiry_days
        FROM deltallm_teamtable
        WHERE team_id = $1
        LIMIT 1
        """,
        team_id,
    )
    if not rows:
        return {"enabled": False}
    row = dict(rows[0])
    return {
        "enabled": bool(row.get("self_service_keys_enabled", False)),
        "max_keys_per_user": row.get("self_service_max_keys_per_user"),
        "budget_ceiling": row.get("self_service_budget_ceiling"),
        "require_expiry": bool(row.get("self_service_require_expiry", False)),
        "max_expiry_days": row.get("self_service_max_expiry_days"),
    }


async def _validate_self_service_constraints(
    db: Any,
    *,
    team_id: str,
    account_id: str,
    policy: dict[str, Any],
    max_budget: Any,
    expires: str | None,
    **kwargs: Any,
) -> None:
    if not policy.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Self-service key creation is not enabled for this team",
        )

    max_keys = policy.get("max_keys_per_user")
    if max_keys is not None:
        count_rows = await db.query_raw(
            "SELECT COUNT(*) AS cnt FROM deltallm_verificationtoken WHERE team_id = $1 AND owner_account_id = $2",
            team_id,
            account_id,
        )
        current_count = int((count_rows[0] if count_rows else {}).get("cnt") or 0)
        if current_count >= max_keys:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"You have reached the maximum of {max_keys} self-service keys for this team",
            )

    budget_ceiling = policy.get("budget_ceiling")
    if budget_ceiling is not None:
        if max_budget is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"A budget (max_budget) is required for self-service keys on this team (ceiling: ${budget_ceiling})",
            )
        try:
            budget_val = float(max_budget)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_budget must be a valid number",
            )
        if budget_val < 0 or budget_val > float(budget_ceiling):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Budget must be between $0 and ${budget_ceiling}",
            )

    require_expiry = policy.get("require_expiry", False)
    max_expiry_days = policy.get("max_expiry_days")
    if require_expiry and not expires:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An expiry date is required for self-service keys on this team",
        )

    team_rows = await db.query_raw(
        "SELECT rpm_limit, tpm_limit, rph_limit, rpd_limit, tpd_limit FROM deltallm_teamtable WHERE team_id = $1 LIMIT 1",
        team_id,
    )
    if team_rows:
        team_limits = dict(team_rows[0])
        for limit_field in ("rpm_limit", "tpm_limit", "rph_limit", "rpd_limit", "tpd_limit"):
            team_val = team_limits.get(limit_field)
            if team_val is not None:
                from_payload = kwargs.get(limit_field)
                if from_payload is not None:
                    try:
                        if int(from_payload) > int(team_val):
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"{limit_field} ({from_payload}) cannot exceed team limit ({team_val})",
                            )
                    except (TypeError, ValueError):
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"{limit_field} must be a valid integer",
                        )

    if max_expiry_days is not None and expires:
        from datetime import datetime, timedelta, timezone
        try:
            exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="expires must be a valid ISO 8601 datetime string",
            )
        max_allowed = datetime.now(timezone.utc) + timedelta(days=max_expiry_days)
        if exp_dt > max_allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Expiry date cannot be more than {max_expiry_days} days from now",
            )


def _is_self_service_only(scope: Any) -> bool:
    if scope.is_platform_admin:
        return False
    return (
        Permission.KEY_CREATE_SELF in scope.granted_permissions
        and Permission.KEY_UPDATE not in scope.granted_permissions
    )


def _key_response_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = to_json_value(dict(row))
    if isinstance(payload, dict):
        payload.pop("models", None)
    return payload


def _reject_legacy_models_field(payload: dict[str, Any]) -> None:
    if "models" in payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="models is no longer supported; use callable-target bindings instead",
        )


async def _get_team_row(db: Any, team_id: str) -> dict[str, Any]:
    rows = await db.query_raw(
        """
        SELECT team_id, team_alias, organization_id
        FROM deltallm_teamtable
        WHERE team_id = $1
        LIMIT 1
        """,
        team_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return dict(rows[0])


async def _get_key_scope_row(db: Any, token_hash: str) -> dict[str, Any]:
    rows = await db.query_raw(
        """
        SELECT
            vt.token,
            vt.user_id,
            COALESCE(vt.team_id, u.team_id) AS team_id,
            t.organization_id
        FROM deltallm_verificationtoken vt
        LEFT JOIN deltallm_usertable u ON u.user_id = vt.user_id
        LEFT JOIN deltallm_teamtable t ON t.team_id = COALESCE(vt.team_id, u.team_id)
        WHERE vt.token = $1
        LIMIT 1
        """,
        token_hash,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    return dict(rows[0])


async def _validate_runtime_user(db: Any, user_id: str, team_id: str | None) -> None:
    await validate_runtime_user_scope(db, user_id, team_id=team_id)


async def _validate_owner_references(
    request: Request,
    db: Any,
    *,
    team_id: str,
    owner_account_id: str | None,
    owner_service_account_id: str | None,
    require_owner: bool,
) -> tuple[str | None, str | None]:
    if owner_account_id and owner_service_account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Select either 'You' or a service account, not both")

    ctx = get_platform_auth_context(request)
    if owner_account_id:
        rows = await db.query_raw(
            "SELECT account_id FROM deltallm_platformaccount WHERE account_id = $1 LIMIT 1",
            owner_account_id,
        )
        if not rows:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="owner_account_id not found")
        if ctx is not None and str(ctx.account_id) != owner_account_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="owner_account_id must match the current account")
        return owner_account_id, None

    if owner_service_account_id:
        rows = await db.query_raw(
            """
            SELECT service_account_id, team_id, is_active
            FROM deltallm_serviceaccount
            WHERE service_account_id = $1
            LIMIT 1
            """,
            owner_service_account_id,
        )
        if not rows:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="owner_service_account_id not found")
        row = rows[0]
        if str(row.get("team_id") or "") != team_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Service account must belong to the selected team")
        if not bool(row.get("is_active", True)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected service account is inactive")
        return None, owner_service_account_id

    if not require_owner:
        return None, None

    if ctx is not None:
        return str(ctx.account_id), None

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="owner is required")


@router.get("/ui/api/keys")
async def list_keys(
    request: Request,
    search: str | None = Query(default=None),
    team_id: str | None = Query(default=None),
    my_keys: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, any_permission=[Permission.KEY_READ, Permission.KEY_CREATE_SELF])
    db = db_or_503(request)
    self_service_only = _is_self_service_only(scope)

    clauses: list[str] = []
    params: list[Any] = []

    if self_service_only or my_keys:
        if scope.account_id:
            params.append(scope.account_id)
            clauses.append(f"vt.owner_account_id = ${len(params)}")
        else:
            return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}

    if not scope.is_platform_admin:
        if scope.org_ids:
            ph = ", ".join(f"${len(params) + i + 1}" for i in range(len(scope.org_ids)))
            params.extend(scope.org_ids)
            clauses.append(f"t.organization_id IN ({ph})")
        elif scope.team_ids:
            ph = ", ".join(f"${len(params) + i + 1}" for i in range(len(scope.team_ids)))
            params.extend(scope.team_ids)
            clauses.append(f"vt.team_id IN ({ph})")
        else:
            return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}

    if search:
        params.append(f"%{search}%")
        clauses.append(f"(vt.key_name ILIKE ${len(params)} OR vt.token ILIKE ${len(params)})")
    if team_id:
        params.append(team_id)
        clauses.append(f"vt.team_id = ${len(params)}")

    join_sql = """
        LEFT JOIN deltallm_teamtable t ON vt.team_id = t.team_id
        LEFT JOIN deltallm_platformaccount pa ON vt.owner_account_id = pa.account_id
        LEFT JOIN deltallm_serviceaccount sa ON vt.owner_service_account_id = sa.service_account_id
    """
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
        SELECT
            vt.token,
            vt.key_name,
            vt.user_id,
            vt.team_id,
            t.team_alias,
            vt.owner_account_id,
            pa.email AS owner_account_email,
            vt.owner_service_account_id,
            sa.name AS owner_service_account_name,
            vt.spend,
            vt.max_budget,
            vt.rpm_limit,
            vt.tpm_limit,
            vt.rph_limit,
            vt.rpd_limit,
            vt.tpd_limit,
            vt.expires,
            vt.created_at,
            vt.updated_at
        FROM deltallm_verificationtoken vt {join_sql}
        {where_sql}
        ORDER BY vt.created_at DESC
        LIMIT ${len(params) - 1} OFFSET ${len(params)}
        """,
        *params,
    )

    return {
        "data": [_key_response_payload(dict(row)) for row in rows],
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
    _reject_legacy_models_field(payload)
    scope = get_auth_scope(request, authorization, x_master_key, any_permission=[Permission.KEY_UPDATE, Permission.KEY_CREATE_SELF])
    db = db_or_503(request)
    self_service_only = _is_self_service_only(scope)

    key_name = str(payload.get("key_name") or "").strip()
    user_id = str(payload.get("user_id") or "").strip() or None
    team_id = str(payload.get("team_id") or "").strip()
    owner_account_id = str(payload.get("owner_account_id") or "").strip() or None
    owner_service_account_id = str(payload.get("owner_service_account_id") or "").strip() or None
    max_budget = payload.get("max_budget")
    rpm_limit = payload.get("rpm_limit")
    tpm_limit = payload.get("tpm_limit")
    rph_limit = payload.get("rph_limit")
    rpd_limit = payload.get("rpd_limit")
    tpd_limit = payload.get("tpd_limit")
    expires = payload.get("expires")
    if expires is not None and not isinstance(expires, str):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="expires must be a string datetime")

    if not key_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="key_name is required")

    if not team_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="team_id is required")

    if self_service_only:
        owner_account_id = scope.account_id
        owner_service_account_id = None
        user_id = None

        if team_id not in scope.team_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only create self-service keys for teams you belong to")

        policy = await _get_self_service_policy(db, team_id)
        await _validate_self_service_constraints(
            db,
            team_id=team_id,
            account_id=scope.account_id,
            policy=policy,
            max_budget=max_budget,
            expires=expires,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
            rph_limit=rph_limit,
            rpd_limit=rpd_limit,
            tpd_limit=tpd_limit,
        )

    team = await _get_team_row(db, team_id)
    team_org = team.get("organization_id")
    if not scope.is_platform_admin and not self_service_only:
        if not team_org or team_org not in scope.org_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only create keys for teams in your organizations")
    if user_id:
        await _validate_runtime_user(db, user_id, team_id)
    if not self_service_only:
        owner_account_id, owner_service_account_id = await _validate_owner_references(
            request,
            db,
            team_id=team_id,
            owner_account_id=owner_account_id,
            owner_service_account_id=owner_service_account_id,
            require_owner=True,
        )

    raw_key = f"sk-{secrets.token_urlsafe(24)}"
    key_service = request.app.state.key_service
    token_hash = key_service.hash_key(raw_key)
    audit_action = AuditAction.ADMIN_KEY_SELF_CREATE if self_service_only else AuditAction.ADMIN_KEY_CREATE

    try:
        await db.execute_raw(
            """
            INSERT INTO deltallm_verificationtoken (
                id, token, key_name, user_id, team_id, owner_account_id, owner_service_account_id,
                spend, max_budget, rpm_limit, tpm_limit, rph_limit, rpd_limit, tpd_limit, expires, created_at, updated_at
            )
            VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, 0, $7, $8, $9, $10, $11, $12, $13::timestamp, NOW(), NOW())
            """,
            token_hash,
            key_name,
            user_id,
            team_id,
            owner_account_id,
            owner_service_account_id,
            max_budget,
            rpm_limit,
            tpm_limit,
            rph_limit,
            rpd_limit,
            tpd_limit,
            expires,
        )

        response = {
            "token": token_hash,
            "raw_key": raw_key,
            "key_name": key_name,
            "user_id": user_id,
            "team_id": team_id,
            "team_alias": team.get("team_alias"),
            "owner_account_id": owner_account_id,
            "owner_service_account_id": owner_service_account_id,
            "max_budget": max_budget,
            "rpm_limit": rpm_limit,
            "tpm_limit": tpm_limit,
            "rph_limit": rph_limit,
            "rpd_limit": rpd_limit,
            "tpd_limit": tpd_limit,
            "expires": expires,
            "self_service": self_service_only,
        }
        await emit_admin_mutation_audit(
            request=request,
            request_start=request_start,
            action=audit_action,
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
            action=audit_action,
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
    _reject_legacy_models_field(payload)
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_UPDATE)
    db = db_or_503(request)
    await _require_key_access(scope, db, token_hash)
    rows = await db.query_raw(
        """
        SELECT token, key_name, user_id, team_id, owner_account_id, owner_service_account_id, spend, max_budget, rpm_limit, tpm_limit, rph_limit, rpd_limit, tpd_limit, expires, created_at, updated_at
        FROM deltallm_verificationtoken
        WHERE token = $1
        LIMIT 1
        """,
        token_hash,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")

    existing = dict(rows[0])
    expires = payload.get("expires", existing.get("expires"))
    if expires is not None and not isinstance(expires, str):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="expires must be a string datetime")

    key_name = str(payload.get("key_name", existing.get("key_name")) or "").strip()
    user_id = str(payload.get("user_id", existing.get("user_id")) or "").strip() or None
    team_id = str(payload.get("team_id", existing.get("team_id")) or "").strip()
    owner_account_id = str(payload.get("owner_account_id", existing.get("owner_account_id")) or "").strip() or None
    owner_service_account_id = str(payload.get("owner_service_account_id", existing.get("owner_service_account_id")) or "").strip() or None
    max_budget = payload.get("max_budget", existing.get("max_budget"))
    rpm_limit = payload.get("rpm_limit", existing.get("rpm_limit"))
    tpm_limit = payload.get("tpm_limit", existing.get("tpm_limit"))
    rph_limit = payload.get("rph_limit", existing.get("rph_limit"))
    rpd_limit = payload.get("rpd_limit", existing.get("rpd_limit"))
    tpd_limit = payload.get("tpd_limit", existing.get("tpd_limit"))

    if not key_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="key_name is required")

    if not team_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="team_id is required")
    team = await _get_team_row(db, team_id)
    if not scope.is_platform_admin:
        team_org = team.get("organization_id")
        if not team_org or team_org not in scope.org_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only manage keys for teams in your organizations")
    if user_id:
        await _validate_runtime_user(db, user_id, team_id)
    owner_account_id, owner_service_account_id = await _validate_owner_references(
        request,
        db,
        team_id=team_id,
        owner_account_id=owner_account_id,
        owner_service_account_id=owner_service_account_id,
        require_owner=False,
    )

    try:
        await db.execute_raw(
            """
            UPDATE deltallm_verificationtoken
            SET key_name = $1,
                user_id = $2,
                team_id = $3,
                owner_account_id = $4,
                owner_service_account_id = $5,
                max_budget = $6,
                rpm_limit = $7,
                tpm_limit = $8,
                rph_limit = $9,
                rpd_limit = $10,
                tpd_limit = $11,
                expires = $12::timestamp,
                updated_at = NOW()
            WHERE token = $13
            """,
            key_name,
            user_id,
            team_id,
            owner_account_id,
            owner_service_account_id,
            max_budget,
            rpm_limit,
            tpm_limit,
            rph_limit,
            rpd_limit,
            tpd_limit,
            expires,
            token_hash,
        )

        key_service = getattr(request.app.state, "key_service", None)
        if key_service:
            await key_service.invalidate_key_cache_by_hash(token_hash)

        updated_rows = await db.query_raw(
            """
            SELECT
                vt.token,
                vt.key_name,
                vt.user_id,
                vt.team_id,
                t.team_alias,
                vt.owner_account_id,
                pa.email AS owner_account_email,
                vt.owner_service_account_id,
                sa.name AS owner_service_account_name,
                vt.spend,
                vt.max_budget,
                vt.rpm_limit,
                vt.tpm_limit,
                vt.rph_limit,
                vt.rpd_limit,
                vt.tpd_limit,
                vt.expires,
                vt.created_at,
                vt.updated_at
            FROM deltallm_verificationtoken vt
            LEFT JOIN deltallm_teamtable t ON vt.team_id = t.team_id
            LEFT JOIN deltallm_platformaccount pa ON vt.owner_account_id = pa.account_id
            LEFT JOIN deltallm_serviceaccount sa ON vt.owner_service_account_id = sa.service_account_id
            WHERE token = $1
            LIMIT 1
            """,
            token_hash,
        )
        if not updated_rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
        updated = _key_response_payload(dict(updated_rows[0]))
        await emit_admin_mutation_audit(
            request=request,
            request_start=request_start,
            action=AuditAction.ADMIN_KEY_UPDATE,
            scope=scope,
            resource_type="api_key",
            resource_id=token_hash,
            request_payload=payload,
            before=_key_response_payload(existing),
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


async def _require_key_access(scope, db, token_hash: str, *, allow_self_service: bool = False) -> None:
    if scope.is_platform_admin:
        return
    row = await _get_key_scope_row(db, token_hash)

    if allow_self_service and _is_self_service_only(scope):
        owner_rows = await db.query_raw(
            "SELECT owner_account_id FROM deltallm_verificationtoken WHERE token = $1 LIMIT 1",
            token_hash,
        )
        if owner_rows and str(owner_rows[0].get("owner_account_id") or "") == scope.account_id:
            return
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only manage your own keys")

    if not row.get("organization_id") or row["organization_id"] not in scope.org_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")


@router.get("/ui/api/keys/{token_hash}/asset-visibility")
async def get_key_asset_visibility(
    request: Request,
    token_hash: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
    db = db_or_503(request)
    await _require_key_access(scope, db, token_hash)
    key_row = await _get_key_scope_row(db, token_hash)
    organization_id = str(key_row.get("organization_id") or "").strip()
    team_id = str(key_row.get("team_id") or "").strip() or None
    user_id = str(key_row.get("user_id") or "").strip() or None
    if not organization_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Key team organization is not configured")
    return await build_asset_visibility_preview(
        request,
        organization_id=organization_id,
        team_id=team_id,
        api_key_id=token_hash,
        user_id=user_id,
    )


@router.get("/ui/api/keys/{token_hash}/asset-access")
async def get_key_asset_access(
    request: Request,
    token_hash: str,
    include_targets: bool = Query(default=True),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
    db = db_or_503(request)
    await _require_key_access(scope, db, token_hash)
    key_row = await _get_key_scope_row(db, token_hash)
    organization_id = str(key_row.get("organization_id") or "").strip()
    team_id = str(key_row.get("team_id") or "").strip() or None
    user_id = str(key_row.get("user_id") or "").strip() or None
    if not organization_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Key team organization is not configured")
    return await build_scope_asset_access(
        request,
        scope_type="api_key",
        scope_id=token_hash,
        organization_id=organization_id,
        team_id=team_id,
        api_key_id=token_hash,
        user_id=user_id,
        include_targets=include_targets,
    )


@router.put("/ui/api/keys/{token_hash}/asset-access")
async def update_key_asset_access(
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
    key_row = await _get_key_scope_row(db, token_hash)
    organization_id = str(key_row.get("organization_id") or "").strip()
    team_id = str(key_row.get("team_id") or "").strip() or None
    user_id = str(key_row.get("user_id") or "").strip() or None
    if not organization_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Key team organization is not configured")
    response = await apply_scope_asset_access(
        request,
        scope_type="api_key",
        scope_id=token_hash,
        organization_id=organization_id,
        team_id=team_id,
        api_key_id=token_hash,
        user_id=user_id,
        mode=payload.get("mode"),
        selected_callable_keys=payload.get("selected_callable_keys", []),
        select_all_selectable=bool(payload.get("select_all_selectable", False)),
    )
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_KEY_ASSET_ACCESS_UPDATE,
        scope=scope,
        resource_type="api_key_asset_access",
        resource_id=token_hash,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.post("/ui/api/keys/{token_hash}/regenerate")
async def regenerate_key(
    request: Request,
    token_hash: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, any_permission=[Permission.KEY_UPDATE, Permission.KEY_CREATE_SELF])
    db = db_or_503(request)
    self_service_only = _is_self_service_only(scope)
    await _require_key_access(scope, db, token_hash, allow_self_service=True)

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
    audit_action = AuditAction.ADMIN_KEY_SELF_ROTATE if self_service_only else AuditAction.ADMIN_KEY_REGENERATE
    response = {"token": new_hash, "raw_key": raw_key}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=audit_action,
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
    scope = get_auth_scope(request, authorization, x_master_key, any_permission=[Permission.KEY_REVOKE, Permission.KEY_CREATE_SELF])
    db = db_or_503(request)
    self_service_only = _is_self_service_only(scope)
    await _require_key_access(scope, db, token_hash, allow_self_service=True)
    deleted = await db.execute_raw("DELETE FROM deltallm_verificationtoken WHERE token = $1", token_hash)
    if int(deleted or 0) > 0:
        try:
            await request.app.state.key_service.invalidate_key_cache_by_hash(token_hash)
        except Exception:
            logger.exception("failed to invalidate key auth cache after revoke")
    audit_action = AuditAction.ADMIN_KEY_SELF_REVOKE if self_service_only else AuditAction.ADMIN_KEY_REVOKE
    response = {"revoked": int(deleted or 0) > 0}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=audit_action,
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
    scope = get_auth_scope(request, authorization, x_master_key, any_permission=[Permission.KEY_REVOKE, Permission.KEY_CREATE_SELF])
    db = db_or_503(request)
    self_service_only = _is_self_service_only(scope)
    await _require_key_access(scope, db, token_hash, allow_self_service=True)
    deleted = await db.execute_raw("DELETE FROM deltallm_verificationtoken WHERE token = $1", token_hash)
    if int(deleted or 0) > 0:
        try:
            await request.app.state.key_service.invalidate_key_cache_by_hash(token_hash)
        except Exception:
            logger.exception("failed to invalidate key auth cache after delete")
    audit_action = AuditAction.ADMIN_KEY_SELF_REVOKE if self_service_only else AuditAction.ADMIN_KEY_DELETE
    response = {"deleted": int(deleted or 0) > 0}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=audit_action,
        scope=scope,
        resource_type="api_key",
        resource_id=token_hash,
        response_payload=response,
    )
    return response
