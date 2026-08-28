from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.auth.roles import (
    Permission,
    PlatformRole,
    validate_organization_role,
    validate_platform_role,
    validate_team_role,
)
from src.audit import AuditAction
from src.api.admin.endpoints.common import db_or_503, emit_admin_mutation_audit, to_json_value
from src.api.admin.organization_mutations import (
    require_active_organization_mutation,
    require_active_organization_mutations,
)
from src.middleware.admin import require_admin_permission
from src.services.access_provisioning_service import AccessProvisioningService

router = APIRouter(tags=["Admin RBAC"])

_SELF_REGISTRATION_SOURCE = "self_registration"
_ACCOUNT_SELF_REGISTRATION_METADATA_KEY = "self_registration"


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _is_self_registration_default(metadata: Any, entity_type: str) -> bool:
    metadata_obj = _json_object(metadata)
    return (
        metadata_obj.get("source") == _SELF_REGISTRATION_SOURCE
        and metadata_obj.get("self_registration_default") == entity_type
    )


def _self_registration_account_metadata(metadata: Any) -> dict[str, Any]:
    metadata_obj = _json_object(metadata)
    registration = metadata_obj.get(_ACCOUNT_SELF_REGISTRATION_METADATA_KEY)
    if not isinstance(registration, dict):
        return {}
    if (
        registration.get("source") != _SELF_REGISTRATION_SOURCE
        or registration.get("registered") is not True
    ):
        return {}
    return registration


def _empty_self_registration_payload(
    account_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    registration = account_metadata or {}
    return {
        "is_self_registered": bool(registration),
        "seeded_user": False,
        "seeded_team": False,
        "seeded_organization": False,
        "sandbox_team_id": registration.get("default_team_id"),
        "sandbox_organization_id": registration.get("default_organization_id"),
    }


def _merge_account_self_registration(
    runtime_payload: dict[str, Any] | None,
    account_metadata: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(runtime_payload, dict):
        return _empty_self_registration_payload(account_metadata)

    return {
        **runtime_payload,
        "is_self_registered": bool(account_metadata)
        or bool(runtime_payload.get("is_self_registered")),
        "sandbox_team_id": runtime_payload.get("sandbox_team_id")
        or account_metadata.get("default_team_id"),
        "sandbox_organization_id": runtime_payload.get("sandbox_organization_id")
        or account_metadata.get("default_organization_id"),
    }


def _self_service_policy_payload(row: dict[str, Any]) -> dict[str, Any] | None:
    team_id = str(row.get("team_id") or "").strip()
    if not team_id:
        return None
    return {
        "team_id": team_id,
        "team_alias": row.get("team_alias"),
        "self_service_keys_enabled": bool(row.get("self_service_keys_enabled")),
        "self_service_max_keys_per_user": row.get("self_service_max_keys_per_user"),
        "self_service_budget_ceiling": row.get("self_service_budget_ceiling"),
        "self_service_require_expiry": bool(row.get("self_service_require_expiry")),
        "self_service_max_expiry_days": row.get("self_service_max_expiry_days"),
    }


def _runtime_user_context(row: dict[str, Any]) -> dict[str, Any]:
    item = to_json_value(dict(row))
    if not isinstance(item, dict):
        return {}

    user_default = _is_self_registration_default(item.pop("user_metadata", None), "user")
    team_default = _is_self_registration_default(item.pop("team_metadata", None), "team")
    organization_default = _is_self_registration_default(
        item.pop("organization_metadata", None),
        "organization",
    )

    runtime_user = {
        "user_id": item.get("user_id"),
        "user_email": item.get("user_email"),
        "team_id": item.get("team_id"),
        "team_alias": item.get("team_alias"),
        "organization_id": item.get("organization_id"),
        "organization_name": item.get("organization_name"),
        "max_budget": item.get("max_budget"),
        "soft_budget": item.get("soft_budget"),
        "spend": item.get("spend"),
        "rpm_limit": item.get("rpm_limit"),
        "tpm_limit": item.get("tpm_limit"),
        "rph_limit": item.get("rph_limit"),
        "rpd_limit": item.get("rpd_limit"),
        "tpd_limit": item.get("tpd_limit"),
        "blocked": bool(item.get("blocked")),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "self_registration_default": user_default,
    }

    return {
        "runtime_user": runtime_user,
        "self_registration": {
            "is_self_registered": user_default,
            "seeded_user": user_default,
            "seeded_team": team_default,
            "seeded_organization": organization_default,
            "sandbox_team_id": item.get("team_id") if team_default else None,
            "sandbox_organization_id": item.get("organization_id")
            if organization_default
            else None,
        },
        "self_service_policy": _self_service_policy_payload(item),
    }


@router.get(
    "/ui/api/rbac/accounts",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
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


@router.get(
    "/ui/api/principals",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
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
        clauses.append(
            f"(email ILIKE ${len(params)} OR role ILIKE ${len(params)} OR account_id::text ILIKE ${len(params)})"
        )
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    count_rows = await db.query_raw(
        f"SELECT COUNT(*) AS total FROM deltallm_platformaccount{where_sql}",
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)

    page_params = [*params, limit, offset]
    account_rows = await db.query_raw(
        f"""
        SELECT account_id, email, role, is_active, force_password_change, mfa_enabled,
               metadata AS account_metadata, created_at, updated_at, last_login_at
        FROM deltallm_platformaccount
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
        """,
        *page_params,
    )
    account_ids = [
        str(row.get("account_id") or "") for row in account_rows if row.get("account_id")
    ]
    if not account_ids:
        return {
            "data": [],
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
            },
        }

    account_ph = ", ".join(f"${i + 1}" for i in range(len(account_ids)))
    org_rows = await db.query_raw(
        f"""
        SELECT om.membership_id, om.account_id, om.organization_id, om.role, om.created_at, om.updated_at,
               o.organization_name, o.metadata AS organization_metadata
        FROM deltallm_organizationmembership om
        LEFT JOIN deltallm_organizationtable o
          ON o.organization_id = om.organization_id
        WHERE om.account_id IN ({account_ph})
        ORDER BY om.created_at DESC
        """,
        *account_ids,
    )
    team_rows = await db.query_raw(
        f"""
        SELECT tm.membership_id, tm.account_id, tm.team_id, tm.role, tm.created_at, tm.updated_at,
               t.team_alias, t.organization_id, t.metadata AS team_metadata,
               t.self_service_keys_enabled, t.self_service_max_keys_per_user,
               t.self_service_budget_ceiling, t.self_service_require_expiry,
               t.self_service_max_expiry_days
        FROM deltallm_teammembership tm
        LEFT JOIN deltallm_teamtable t
          ON t.team_id = tm.team_id
        WHERE tm.account_id IN ({account_ph})
        ORDER BY tm.created_at DESC
        """,
        *account_ids,
    )

    runtime_params: list[Any] = []
    runtime_account_values: list[str] = []
    for row in account_rows:
        account_id = str(row.get("account_id") or "").strip()
        if not account_id:
            continue
        placeholder_index = len(runtime_params) + 1
        runtime_params.extend([account_id, str(row.get("email") or "").strip().lower()])
        runtime_account_values.append(
            f"(${placeholder_index}::text, ${placeholder_index + 1}::text)"
        )

    runtime_user_rows = await db.query_raw(
        f"""
        WITH page_accounts(account_id, account_email) AS (
            VALUES {", ".join(runtime_account_values)}
        ),
        matched_runtime_users AS (
            SELECT p.account_id AS matched_account_id, 0 AS match_rank, u.user_id
            FROM page_accounts p
            JOIN deltallm_usertable u
              ON u.user_id = p.account_id
            UNION ALL
            SELECT p.account_id AS matched_account_id, 1 AS match_rank, u.user_id
            FROM page_accounts p
            JOIN deltallm_usertable u
              ON u.user_email = p.account_email
            WHERE p.account_email <> ''
            UNION ALL
            SELECT p.account_id AS matched_account_id, 2 AS match_rank, u.user_id
            FROM page_accounts p
            JOIN deltallm_usertable u
              ON lower(u.user_email) = p.account_email
            WHERE p.account_email <> ''
              AND u.user_email IS DISTINCT FROM p.account_email
        ),
        ranked_runtime_users AS (
            SELECT DISTINCT ON (matched_account_id)
                   matched_account_id, user_id
            FROM matched_runtime_users
            ORDER BY matched_account_id, match_rank, user_id
        )
        SELECT r.matched_account_id, u.user_id, u.user_email, u.team_id, u.max_budget, u.soft_budget, u.spend,
               u.rpm_limit, u.tpm_limit, u.rph_limit, u.rpd_limit, u.tpd_limit,
               u.blocked, u.metadata AS user_metadata, u.created_at, u.updated_at,
               t.organization_id, t.team_alias,
               t.self_service_keys_enabled, t.self_service_max_keys_per_user,
               t.self_service_budget_ceiling, t.self_service_require_expiry,
               t.self_service_max_expiry_days, t.metadata AS team_metadata,
               o.organization_name, o.metadata AS organization_metadata
        FROM ranked_runtime_users r
        JOIN deltallm_usertable u
          ON u.user_id = r.user_id
        LEFT JOIN deltallm_teamtable t
          ON t.team_id = u.team_id
        LEFT JOIN deltallm_organizationtable o
          ON o.organization_id = t.organization_id
        """,
        *runtime_params,
    )

    org_by_account: dict[str, list[dict[str, Any]]] = {}
    for row in org_rows:
        item = to_json_value(dict(row))
        if not isinstance(item, dict):
            continue
        item["self_registration_default"] = _is_self_registration_default(
            item.pop("organization_metadata", None),
            "organization",
        )
        account_id = str(item.get("account_id") or "")
        if not account_id:
            continue
        org_by_account.setdefault(account_id, []).append(item)

    team_by_account: dict[str, list[dict[str, Any]]] = {}
    for row in team_rows:
        item = to_json_value(dict(row))
        if not isinstance(item, dict):
            continue
        item["self_registration_default"] = _is_self_registration_default(
            item.pop("team_metadata", None),
            "team",
        )
        account_id = str(item.get("account_id") or "")
        if not account_id:
            continue
        team_by_account.setdefault(account_id, []).append(item)

    runtime_context_by_account: dict[str, dict[str, Any]] = {}
    for row in runtime_user_rows:
        item = dict(row)
        account_id = str(item.pop("matched_account_id", "") or "").strip()
        if not account_id:
            continue
        runtime_context_by_account[account_id] = _runtime_user_context(item)

    principals: list[dict[str, Any]] = []
    for row in account_rows:
        base = to_json_value(dict(row))
        if not isinstance(base, dict):
            continue

        account_id = str(base.get("account_id") or "")
        account_metadata_value = base.pop("account_metadata", None)
        if account_metadata_value is None:
            account_metadata_value = base.pop("metadata", None)
        else:
            base.pop("metadata", None)
        account_metadata = _self_registration_account_metadata(account_metadata_value)
        org_memberships = org_by_account.get(account_id, [])
        team_memberships = team_by_account.get(account_id, [])
        runtime_context = runtime_context_by_account.get(account_id, {})
        runtime_user = runtime_context.get("runtime_user") if runtime_context else None
        self_registration = _merge_account_self_registration(
            runtime_context.get("self_registration") if runtime_context else None,
            account_metadata,
        )

        principals.append(
            {
                **base,
                "runtime_user_id": runtime_user.get("user_id")
                if isinstance(runtime_user, dict)
                else None,
                "runtime_user": runtime_user,
                "self_registration": self_registration,
                "self_service_policy": runtime_context.get("self_service_policy")
                if runtime_context
                else None,
                "organization_memberships": org_memberships,
                "team_memberships": team_memberships,
            }
        )

    return {
        "data": principals,
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
    }


@router.get(
    "/ui/api/principals/summary",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def principals_summary(request: Request) -> dict[str, int]:
    db = db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT
            COUNT(*)::int AS total_accounts,
            COUNT(*) FILTER (WHERE is_active)::int AS active_accounts,
            COUNT(*) FILTER (WHERE role = $1)::int AS platform_admins,
            COUNT(*) FILTER (WHERE mfa_enabled)::int AS mfa_enabled_accounts
        FROM deltallm_platformaccount
        """,
        PlatformRole.ADMIN,
    )
    membership_rows = await db.query_raw(
        """
        SELECT
            (SELECT COUNT(*)::int FROM deltallm_organizationmembership) AS organization_memberships,
            (SELECT COUNT(*)::int FROM deltallm_teammembership) AS team_memberships
        """
    )
    base = rows[0] if rows else {}
    memberships = membership_rows[0] if membership_rows else {}
    return {
        "total_accounts": int(base.get("total_accounts") or 0),
        "active_accounts": int(base.get("active_accounts") or 0),
        "platform_admins": int(base.get("platform_admins") or 0),
        "mfa_enabled_accounts": int(base.get("mfa_enabled_accounts") or 0),
        "organization_memberships": int(memberships.get("organization_memberships") or 0),
        "team_memberships": int(memberships.get("team_memberships") or 0),
    }


@router.post(
    "/ui/api/rbac/accounts",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def upsert_rbac_account(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    service = getattr(request.app.state, "platform_identity_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auth service unavailable"
        )

    email = str(payload.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="email is required")

    try:
        role = validate_platform_role(payload.get("role") or "org_user")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    is_active = bool(payload.get("is_active", True))
    password = payload.get("password")
    if isinstance(password, str) and password:
        try:
            service.validate_password_policy(password)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

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
            try:
                updated = await service.admin_set_password(
                    account_id=rows[0]["account_id"], new_password=password
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
                ) from exc
            if not updated:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="failed to set account password",
                )

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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="account upsert failed"
        )
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


@router.post(
    "/ui/api/rbac/provision",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def provision_person(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    identity_service = getattr(request.app.state, "platform_identity_service", None)
    invitation_service = getattr(request.app.state, "invitation_service", None)
    if identity_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Provisioning service unavailable",
        )

    mode = str(payload.get("mode") or "").strip()
    if mode == "invite_email" and invitation_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Invitation service unavailable"
        )

    service = AccessProvisioningService(
        db_client=db,
        platform_identity_service=identity_service,
        invitation_service=invitation_service,
    )

    try:
        response = await service.provision_person(
            email=str(payload.get("email") or ""),
            mode=mode,
            platform_role=str(
                payload.get("platform_role") or payload.get("role") or PlatformRole.ORG_USER
            ),
            password=str(payload.get("password") or "") or None,
            is_active=bool(payload.get("is_active", True)),
            organization_id=str(payload.get("organization_id") or "").strip() or None,
            organization_role=str(payload.get("organization_role") or "") or None,
            team_id=str(payload.get("team_id") or "").strip() or None,
            team_role=str(payload.get("team_role") or "") or None,
            invited_by_account_id=getattr(
                getattr(request.state, "platform_auth", None), "account_id", None
            ),
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = (
            status.HTTP_404_NOT_FOUND if "not found" in detail else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except RuntimeError as exc:
        if "invitation service unavailable" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Invitation service unavailable",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Provisioning failed"
        ) from exc

    mode = str(response.get("mode") or "")
    action = (
        AuditAction.ADMIN_INVITATION_CREATE
        if mode == "invite_email"
        else AuditAction.ADMIN_RBAC_ACCOUNT_UPSERT
    )
    resource_type = "invitation" if mode == "invite_email" else "platform_account"
    resource_id = str(response.get("invitation_id") or response.get("account_id") or "")
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.delete(
    "/ui/api/rbac/accounts/{account_id}",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def delete_rbac_account(request: Request, account_id: str) -> dict[str, bool]:
    request_start = perf_counter()
    db = db_or_503(request)
    if not hasattr(db, "tx"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RBAC mutation requires transaction support",
        )
    async with db.tx() as tx:
        existing = await tx.query_raw(
            """
            SELECT account_id, email, role, is_active
            FROM deltallm_platformaccount
            WHERE account_id = $1
            LIMIT 1
            FOR UPDATE
            """,
            account_id,
        )
        if not existing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
        organization_rows = await tx.query_raw(
            """
            SELECT DISTINCT organization_id
            FROM (
                SELECT organization_id
                FROM deltallm_organizationmembership
                WHERE account_id = $1
                UNION
                SELECT t.organization_id
                FROM deltallm_teammembership tm
                JOIN deltallm_teamtable t ON t.team_id = tm.team_id
                WHERE tm.account_id = $1
            ) account_organizations
            WHERE organization_id IS NOT NULL
            """,
            account_id,
        )
        await require_active_organization_mutations(
            tx,
            {str(row.get("organization_id") or "") for row in organization_rows},
        )

        # Manual delete order keeps behavior deterministic regardless of FK cascade configuration.
        await tx.execute_raw(
            "DELETE FROM deltallm_teammembership WHERE account_id = $1", account_id
        )
        await tx.execute_raw(
            "DELETE FROM deltallm_organizationmembership WHERE account_id = $1", account_id
        )
        await tx.execute_raw(
            "DELETE FROM deltallm_platformsession WHERE account_id = $1", account_id
        )
        await tx.execute_raw(
            "DELETE FROM deltallm_platformidentity WHERE account_id = $1", account_id
        )
        deleted = await tx.execute_raw(
            "DELETE FROM deltallm_platformaccount WHERE account_id = $1", account_id
        )
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


@router.get(
    "/ui/api/rbac/organization-memberships",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def list_org_memberships(
    request: Request, account_id: str | None = None
) -> list[dict[str, Any]]:
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


@router.post(
    "/ui/api/rbac/organization-memberships",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def upsert_org_membership(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    account_id = payload.get("account_id")
    email = payload.get("email")
    organization_id = str(payload.get("organization_id") or "").strip()
    try:
        role = validate_organization_role(payload.get("role") or "org_member")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if not organization_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="organization_id is required"
        )

    if not account_id and email:
        rows = await db.query_raw(
            "SELECT account_id FROM deltallm_platformaccount WHERE lower(email)=lower($1) LIMIT 1",
            email,
        )
        if rows:
            account_id = rows[0].get("account_id")
    if not account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="account_id or known email is required"
        )

    if not hasattr(db, "tx"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Organization membership mutation requires transaction support",
        )
    async with db.tx() as tx:
        await require_active_organization_mutation(tx, organization_id)
        await tx.execute_raw(
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

        rows = await tx.query_raw(
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="membership upsert failed"
        )
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


@router.delete(
    "/ui/api/rbac/organization-memberships/{membership_id}",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def delete_org_membership(request: Request, membership_id: str) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    if not hasattr(db, "tx"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Organization membership mutation requires transaction support",
        )
    async with db.tx() as tx:
        existing_rows = await tx.query_raw(
            """
            SELECT membership_id, account_id, organization_id, role, created_at, updated_at
            FROM deltallm_organizationmembership
            WHERE membership_id = $1
            LIMIT 1
            FOR UPDATE
            """,
            membership_id,
        )
        if not existing_rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Organization membership not found"
            )

        existing = dict(existing_rows[0])
        account_id = str(existing.get("account_id") or "")
        organization_id = str(existing.get("organization_id") or "")
        await require_active_organization_mutation(tx, organization_id)
        removed_team_memberships = await tx.execute_raw(
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

        deleted = await tx.execute_raw(
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


@router.get(
    "/ui/api/rbac/team-memberships",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def list_team_memberships(
    request: Request, account_id: str | None = None
) -> list[dict[str, Any]]:
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


@router.post(
    "/ui/api/rbac/team-memberships",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def upsert_team_membership(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    db = db_or_503(request)
    account_id = payload.get("account_id")
    email = payload.get("email")
    team_id = str(payload.get("team_id") or "").strip()
    try:
        role = validate_team_role(payload.get("role") or "team_viewer")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if not team_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="team_id is required")

    if not account_id and email:
        rows = await db.query_raw(
            "SELECT account_id FROM deltallm_platformaccount WHERE lower(email)=lower($1) LIMIT 1",
            email,
        )
        if rows:
            account_id = rows[0].get("account_id")
    if not account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="account_id or known email is required"
        )

    if not hasattr(db, "tx"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Team membership mutation requires transaction support",
        )
    async with db.tx() as tx:
        account_rows = await tx.query_raw(
            "SELECT account_id FROM deltallm_platformaccount WHERE account_id = $1 LIMIT 1",
            account_id,
        )
        if not account_rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

        team_rows = await tx.query_raw(
            "SELECT team_id, organization_id FROM deltallm_teamtable WHERE team_id = $1 LIMIT 1 FOR SHARE",
            team_id,
        )
        if not team_rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

        organization_id = str(team_rows[0].get("organization_id") or "").strip()
        if organization_id:
            await require_active_organization_mutation(tx, organization_id)
            org_membership_rows = await tx.query_raw(
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
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Account must be a member of the team's organization",
                )

        await tx.execute_raw(
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

        rows = await tx.query_raw(
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="membership upsert failed"
        )
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


@router.delete(
    "/ui/api/rbac/team-memberships/{membership_id}",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def delete_team_membership(request: Request, membership_id: str) -> dict[str, bool]:
    request_start = perf_counter()
    db = db_or_503(request)
    if not hasattr(db, "tx"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Team membership mutation requires transaction support",
        )
    async with db.tx() as tx:
        existing_rows = await tx.query_raw(
            """
            SELECT tm.membership_id, t.organization_id
            FROM deltallm_teammembership tm
            JOIN deltallm_teamtable t ON t.team_id = tm.team_id
            WHERE tm.membership_id = $1
            FOR UPDATE OF tm
            FOR SHARE OF t
            """,
            membership_id,
        )
        if existing_rows:
            await require_active_organization_mutation(
                tx,
                str(existing_rows[0].get("organization_id") or ""),
            )
            deleted = await tx.execute_raw(
                "DELETE FROM deltallm_teammembership WHERE membership_id = $1",
                membership_id,
            )
        else:
            deleted = 0
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
