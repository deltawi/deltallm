from __future__ import annotations

import secrets
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from src.auth.roles import Permission
from src.audit.actions import AuditAction
from src.api.admin.endpoints.common import db_or_503, emit_admin_mutation_audit, get_auth_scope, to_json_value
from src.middleware.platform_auth import get_platform_auth_context

router = APIRouter(tags=["Admin Service Accounts"])


async def _validate_team_access(scope: Any, db: Any, team_id: str) -> dict[str, Any]:
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

    team = dict(rows[0])
    if scope.is_platform_admin:
        return team

    organization_id = str(team.get("organization_id") or "")
    if not organization_id or organization_id not in scope.org_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only manage service accounts for teams in your organizations")
    return team


@router.get("/ui/api/service-accounts")
async def list_service_accounts(
    request: Request,
    team_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
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
            placeholders = ", ".join(f"${len(params) + i + 1}" for i in range(len(scope.org_ids)))
            params.extend(scope.org_ids)
            clauses.append(f"t.organization_id IN ({placeholders})")
        else:
            return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}

    if team_id:
        params.append(team_id)
        clauses.append(f"sa.team_id = ${len(params)}")

    if search:
        params.append(f"%{search.strip()}%")
        clauses.append(f"(sa.name ILIKE ${len(params)} OR sa.service_account_id ILIKE ${len(params)})")

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    count_rows = await db.query_raw(
        f"""
        SELECT COUNT(*) AS total
        FROM deltallm_serviceaccount sa
        JOIN deltallm_teamtable t ON t.team_id = sa.team_id
        {where_sql}
        """,
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)

    page_params = [*params, limit, offset]
    rows = await db.query_raw(
        f"""
        SELECT
            sa.service_account_id,
            sa.team_id,
            sa.name,
            sa.description,
            sa.is_active,
            sa.metadata,
            sa.created_by_account_id,
            sa.created_at,
            sa.updated_at,
            t.team_alias
        FROM deltallm_serviceaccount sa
        JOIN deltallm_teamtable t ON t.team_id = sa.team_id
        {where_sql}
        ORDER BY sa.created_at DESC
        LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
        """,
        *page_params,
    )

    return {
        "data": [to_json_value(dict(row)) for row in rows],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.post("/ui/api/service-accounts")
async def create_service_account(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.TEAM_UPDATE)
    db = db_or_503(request)

    team_id = str(payload.get("team_id") or "").strip()
    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip() or None
    service_account_id = str(payload.get("service_account_id") or f"svc-{secrets.token_hex(8)}")

    if not team_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="team_id is required")
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")

    team = await _validate_team_access(scope, db, team_id)
    existing = await db.query_raw(
        """
        SELECT service_account_id
        FROM deltallm_serviceaccount
        WHERE team_id = $1 AND lower(name) = lower($2)
        LIMIT 1
        """,
        team_id,
        name,
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A service account with this name already exists in the selected team")

    ctx = get_platform_auth_context(request)
    created_by_account_id = getattr(ctx, "account_id", None)

    await db.execute_raw(
        """
        INSERT INTO deltallm_serviceaccount (
            service_account_id, team_id, name, description, is_active, metadata, created_by_account_id, created_at, updated_at
        )
        VALUES ($1, $2, $3, $4, true, '{}'::jsonb, $5, NOW(), NOW())
        """,
        service_account_id,
        team_id,
        name,
        description,
        created_by_account_id,
    )

    response = {
        "service_account_id": service_account_id,
        "team_id": team_id,
        "team_alias": team.get("team_alias"),
        "name": name,
        "description": description,
        "is_active": True,
        "created_by_account_id": created_by_account_id,
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_SERVICE_ACCOUNT_CREATE,
        scope=scope,
        resource_type="service_account",
        resource_id=service_account_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


__all__ = ["router"]
