"""Admin MCP approval-request routes."""

from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from src.api.admin.endpoints.common import emit_admin_mutation_audit, get_auth_scope
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.db.mcp.mcp_approvals import (
    APPROVAL_SELECT_COLUMNS,
    APPROVAL_STATUS_FILTERS,
    approval_status_clause,
    to_approval_request_record,
)
from src.mcp.approvals import MCPApprovalService
from src.mcp.metrics import record_mcp_approval_decision
from src.middleware.admin import require_admin_permission
from src.middleware.platform_auth import get_platform_auth_context

from src.api.admin.endpoints.mcp.dependencies import _db_or_503, _repository_or_503
from src.api.admin.endpoints.mcp.loaders import (
    _load_approval_request_or_404,
    _load_server_or_404,
    _load_server_summary_map,
)
from src.api.admin.endpoints.mcp.scope_visibility import _approval_visible_to_scope
from src.api.admin.endpoints.mcp.serializers import _serialize_approval_request
from src.api.admin.endpoints.mcp.sql_visibility import _approval_visibility_clause

router = APIRouter(tags=["Admin MCP"])

_EXPIRED_DETAIL = "MCP approval request has expired. The caller must send the call again."


@router.get(
    "/ui/api/mcp-approval-requests",
    dependencies=[Depends(require_admin_permission(Permission.KEY_UPDATE))],
)
async def list_mcp_approval_requests(
    request: Request,
    server_id: str | None = Query(default=None),
    status_value: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    repository = _repository_or_503(request)
    scope = get_auth_scope(
        request, authorization, x_master_key, required_permission=Permission.KEY_UPDATE
    )
    viewer_account_id = getattr(get_platform_auth_context(request), "account_id", None)
    if status_value is not None and status_value not in APPROVAL_STATUS_FILTERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status must be pending, approved, rejected, or expired",
        )
    if scope.is_platform_admin:
        approvals, total = await repository.list_approval_requests(
            server_id=server_id,
            status=status_value,
            limit=limit,
            offset=offset,
        )
        servers = await _load_server_summary_map(
            request, [item.mcp_server_id for item in approvals]
        )
        return {
            "data": [
                _serialize_approval_request(
                    item,
                    server=servers.get(item.mcp_server_id),
                    can_decide=item.status == "pending",
                    viewer_account_id=viewer_account_id,
                )
                for item in approvals
            ],
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
            },
        }

    if not scope.org_ids and not scope.team_ids:
        return {
            "data": [],
            "pagination": {"total": 0, "limit": limit, "offset": offset, "has_more": False},
        }

    db = _db_or_503(request)
    clauses: list[str] = []
    params: list[Any] = []
    if server_id:
        params.append(server_id)
        clauses.append(f"r.mcp_server_id = ${len(params)}")
    if status_value:
        clauses.append(approval_status_clause(status_value, params))
    clauses.append(f"({_approval_visibility_clause(scope, params)})")
    where_sql = f" WHERE {' AND '.join(clauses)}"

    count_rows = await db.query_raw(
        f"SELECT COUNT(*)::int AS total FROM deltallm_mcpapprovalrequest r {where_sql}",
        *params,
    )
    total = int((count_rows[0] if count_rows else {}).get("total") or 0)

    page_params = [*params, limit, offset]
    rows = await db.query_raw(
        f"""
        SELECT {APPROVAL_SELECT_COLUMNS}
        FROM deltallm_mcpapprovalrequest r
        {where_sql}
        ORDER BY r.created_at DESC, r.mcp_approval_request_id ASC
        LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
        """,
        *page_params,
    )
    approvals = [to_approval_request_record(row) for row in rows]
    servers = await _load_server_summary_map(request, [item.mcp_server_id for item in approvals])
    return {
        "data": [
            _serialize_approval_request(
                item,
                server=servers.get(item.mcp_server_id),
                can_decide=item.status == "pending",
                viewer_account_id=viewer_account_id,
            )
            for item in approvals
        ],
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
    }


@router.post(
    "/ui/api/mcp-approval-requests/{approval_request_id}/decision",
    dependencies=[Depends(require_admin_permission(Permission.KEY_UPDATE))],
)
async def decide_mcp_approval_request(
    request: Request,
    approval_request_id: str,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    approval_service = MCPApprovalService(repository)
    scope = get_auth_scope(
        request, authorization, x_master_key, required_permission=Permission.KEY_UPDATE
    )
    decision = str(payload.get("status") or "").strip().lower()
    if decision not in {"approved", "rejected"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="status must be approved or rejected"
        )
    existing = await _load_approval_request_or_404(request, approval_request_id)
    if not await _approval_visible_to_scope(request, scope, existing):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
        )
    if existing.status == "expired":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_EXPIRED_DETAIL,
        )
    context = get_platform_auth_context(request)
    decider_account_id = getattr(context, "account_id", None)
    if (
        decision == "approved"
        and decider_account_id
        and decider_account_id == existing.requested_by_account_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot approve an MCP tool call that you requested",
        )
    decided = await repository.decide_approval_request(
        approval_request_id,
        status=decision,
        decided_by_account_id=decider_account_id,
        decision_comment=str(payload.get("decision_comment")).strip()
        if payload.get("decision_comment") is not None
        else None,
        expires_at=approval_service.decision_expiry_for_status(decision),
    )
    if decided is None:
        current = await repository.get_approval_request(approval_request_id)
        if current is not None and current.status == "expired":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_EXPIRED_DETAIL,
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pending MCP approval request not found"
        )
    record_mcp_approval_decision(status=decision)
    server = await _load_server_or_404(request, decided.mcp_server_id)
    response = _serialize_approval_request(decided, server=server, can_decide=False)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MCP_APPROVAL_DECIDE,
        scope=scope,
        resource_type="mcp_approval_request",
        resource_id=approval_request_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


__all__ = ["router"]
