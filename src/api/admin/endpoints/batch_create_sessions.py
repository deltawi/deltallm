from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from src.api.admin.endpoints.common import db_or_503, emit_admin_mutation_audit, get_auth_scope, to_json_value
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.batch.create.admin_service import BatchCreateSessionAdminService
from src.services.ui_authorization import build_batch_create_session_capabilities

router = APIRouter(tags=["Admin Batch Create Sessions"])

_VALID_CREATE_SESSION_STATUSES = {
    "staged",
    "completed",
    "failed_retryable",
    "failed_permanent",
    "expired",
}


def _batch_create_session_admin_service_or_503(request: Request) -> BatchCreateSessionAdminService:
    service = getattr(request.app.state, "batch_create_session_admin_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Batch create-session admin service unavailable",
        )
    return service


def _batch_create_session_admin_actions_enabled(request: Request) -> bool:
    return getattr(request.app.state, "batch_create_session_admin_service", None) is not None


def _mask_api_key(raw_api_key: str | None) -> str:
    api_key = str(raw_api_key or "")
    if len(api_key) > 12:
        return f"{api_key[:8]}...{api_key[-4:]}"
    return api_key


def _append_session_scope_clause(*, clauses: list[str], params: list[Any], scope, session_alias: str = "s.") -> bool:  # noqa: ANN001
    if scope.is_platform_admin:
        return True

    team_column = f"{session_alias}created_by_team_id"
    org_column = f"COALESCE({session_alias}created_by_organization_id, t.organization_id)"
    scope_clauses: list[str] = []

    if scope.team_ids:
        team_placeholders = ", ".join(f"${len(params) + index + 1}" for index in range(len(scope.team_ids)))
        params.extend(scope.team_ids)
        scope_clauses.append(f"{team_column} IN ({team_placeholders})")

    if scope.org_ids:
        org_placeholders = ", ".join(f"${len(params) + index + 1}" for index in range(len(scope.org_ids)))
        params.extend(scope.org_ids)
        scope_clauses.append(
            f"({org_column} IN ({org_placeholders}) "
            f"OR {team_column} IN (SELECT team_id FROM deltallm_teamtable WHERE organization_id IN ({org_placeholders})))"
        )

    if not scope_clauses:
        return False

    clauses.append("(" + " OR ".join(scope_clauses) + ")")
    return True


async def _load_session_scope_row(db: Any, session_id: str) -> dict[str, Any]:
    rows = await db.query_raw(
        """
        SELECT
            s.session_id,
            s.target_batch_id,
            s.status,
            s.created_by_team_id,
            COALESCE(s.created_by_organization_id, t.organization_id) AS organization_id
        FROM deltallm_batch_create_session s
        LEFT JOIN deltallm_teamtable t ON t.team_id = s.created_by_team_id
        WHERE s.session_id = $1
        LIMIT 1
        """,
        session_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch create session not found")
    return dict(rows[0])


async def _enforce_session_update_scope(*, db: Any, scope, session: dict[str, Any]) -> None:  # noqa: ANN001
    if scope.is_platform_admin:
        return
    team_id = str(session.get("created_by_team_id") or "").strip()
    organization_id = str(session.get("organization_id") or "").strip()
    if team_id and scope.team_ids and team_id in scope.team_ids:
        return
    if organization_id and scope.org_ids and organization_id in scope.org_ids:
        return
    if team_id and scope.org_ids:
        org_rows = await db.query_raw(
            "SELECT organization_id FROM deltallm_teamtable WHERE team_id = $1 LIMIT 1",
            team_id,
        )
        org_id = str((org_rows[0] if org_rows else {}).get("organization_id") or "")
        if org_id in scope.org_ids:
            return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


def _session_payload(
    row: dict[str, Any],
    scope,
    *,
    admin_actions_enabled: bool,
) -> dict[str, Any]:  # noqa: ANN001
    payload = {
        "session_id": row.get("session_id"),
        "target_batch_id": row.get("target_batch_id"),
        "status": row.get("status"),
        "endpoint": row.get("endpoint"),
        "input_file_id": row.get("input_file_id"),
        "expected_item_count": int(row.get("expected_item_count") or 0),
        "inferred_model": row.get("inferred_model"),
        "requested_service_tier": row.get("requested_service_tier"),
        "effective_service_tier": row.get("effective_service_tier"),
        "created_by_api_key": _mask_api_key(row.get("created_by_api_key")),
        "created_by_user_id": row.get("created_by_user_id"),
        "created_by_team_id": row.get("created_by_team_id"),
        "created_by_organization_id": row.get("created_by_organization_id") or row.get("organization_id"),
        "team_alias": row.get("team_alias"),
        "last_error_code": row.get("last_error_code"),
        "last_error_message": row.get("last_error_message"),
        "promotion_attempt_count": int(row.get("promotion_attempt_count") or 0),
        "created_at": to_json_value(row.get("created_at")),
        "completed_at": to_json_value(row.get("completed_at")),
        "expires_at": to_json_value(row.get("expires_at")),
    }
    payload["capabilities"] = build_batch_create_session_capabilities(
        scope,
        {
            "status": row.get("status"),
            "created_by_team_id": row.get("created_by_team_id"),
            "organization_id": row.get("created_by_organization_id") or row.get("organization_id"),
        },
        admin_actions_enabled=admin_actions_enabled,
    )
    return payload


@router.get("/ui/api/batch-create-sessions")
async def list_batch_create_sessions(
    request: Request,
    search: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=10, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
    db = db_or_503(request)
    admin_actions_enabled = _batch_create_session_admin_actions_enabled(request)

    clauses: list[str] = []
    params: list[Any] = []
    if not _append_session_scope_clause(clauses=clauses, params=params, scope=scope):
        return {"data": [], "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False}}

    if search:
        params.append(f"%{search}%")
        clauses.append(
            f"(s.session_id ILIKE ${len(params)} OR s.target_batch_id ILIKE ${len(params)} OR s.input_file_id ILIKE ${len(params)})"
        )

    if status_filter and status_filter in _VALID_CREATE_SESSION_STATUSES:
        params.append(status_filter)
        clauses.append(f"s.status = ${len(params)}")

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    count_rows = await db.query_raw(
        f"""
        SELECT COUNT(*) AS total
        FROM deltallm_batch_create_session s
        LEFT JOIN deltallm_teamtable t ON t.team_id = s.created_by_team_id
        {where_sql}
        """,
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)

    params.append(limit)
    params.append(offset)
    rows = await db.query_raw(
        f"""
        SELECT
            s.session_id,
            s.target_batch_id,
            s.status,
            s.endpoint,
            s.input_file_id,
            s.expected_item_count,
            s.inferred_model,
            s.requested_service_tier,
            s.effective_service_tier,
            s.created_by_api_key,
            s.created_by_user_id,
            s.created_by_team_id,
            s.created_by_organization_id,
            s.last_error_code,
            s.last_error_message,
            s.promotion_attempt_count,
            s.created_at,
            s.completed_at,
            s.expires_at,
            t.team_alias,
            COALESCE(s.created_by_organization_id, t.organization_id) AS organization_id
        FROM deltallm_batch_create_session s
        LEFT JOIN deltallm_teamtable t ON t.team_id = s.created_by_team_id
        {where_sql}
        ORDER BY s.created_at DESC
        LIMIT ${len(params) - 1} OFFSET ${len(params)}
        """,
        *params,
    )

    data = [_session_payload(dict(row), scope, admin_actions_enabled=admin_actions_enabled) for row in rows]
    return {
        "data": data,
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.get("/ui/api/batch-create-sessions/{session_id}")
async def get_batch_create_session(
    request: Request,
    session_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_READ)
    db = db_or_503(request)
    admin_actions_enabled = _batch_create_session_admin_actions_enabled(request)

    rows = await db.query_raw(
        """
        SELECT
            s.session_id,
            s.target_batch_id,
            s.status,
            s.endpoint,
            s.input_file_id,
            s.expected_item_count,
            s.inferred_model,
            s.requested_service_tier,
            s.effective_service_tier,
            s.created_by_api_key,
            s.created_by_user_id,
            s.created_by_team_id,
            s.created_by_organization_id,
            s.last_error_code,
            s.last_error_message,
            s.promotion_attempt_count,
            s.created_at,
            s.completed_at,
            s.expires_at,
            t.team_alias,
            COALESCE(s.created_by_organization_id, t.organization_id) AS organization_id
        FROM deltallm_batch_create_session s
        LEFT JOIN deltallm_teamtable t ON t.team_id = s.created_by_team_id
        WHERE s.session_id = $1
        LIMIT 1
        """,
        session_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch create session not found")
    row = dict(rows[0])
    await _enforce_session_update_scope(db=db, scope=scope, session=row)
    return _session_payload(row, scope, admin_actions_enabled=admin_actions_enabled)


@router.post("/ui/api/batch-create-sessions/{session_id}/retry")
async def retry_batch_create_session(
    request: Request,
    session_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_UPDATE)
    db = db_or_503(request)
    service = _batch_create_session_admin_service_or_503(request)
    session = await _load_session_scope_row(db, session_id)
    await _enforce_session_update_scope(db=db, scope=scope, session=session)

    result = await service.retry_session(session_id)
    response = {
        "session_id": result.session.session_id,
        "target_batch_id": result.session.target_batch_id,
        "status": result.session.status,
        "retried": True,
        "promotion_result": "promoted" if result.promotion.promoted else "existing_batch",
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_BATCH_CREATE_SESSION_RETRY,
        scope=scope,
        resource_type="batch_create_session",
        resource_id=session_id,
        request_payload={"session_id": session_id},
        response_payload=response,
    )
    return response


@router.post("/ui/api/batch-create-sessions/{session_id}/expire")
async def expire_batch_create_session(
    request: Request,
    session_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.KEY_UPDATE)
    db = db_or_503(request)
    service = _batch_create_session_admin_service_or_503(request)
    session = await _load_session_scope_row(db, session_id)
    await _enforce_session_update_scope(db=db, scope=scope, session=session)

    result = await service.expire_session(session_id)
    response = {
        "session_id": result.session.session_id,
        "target_batch_id": result.session.target_batch_id,
        "status": result.session.status,
        "expired": True,
        "artifact_deleted": result.artifact_deleted,
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_BATCH_CREATE_SESSION_EXPIRE,
        scope=scope,
        resource_type="batch_create_session",
        resource_id=session_id,
        request_payload={"session_id": session_id},
        response_payload=response,
    )
    return response
