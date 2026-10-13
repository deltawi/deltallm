"""Read-only request logs for an external customer workspace session."""

from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request

from src.api.admin.endpoints.common import db_or_503, get_auth_scope, log_admin_query_timing
from src.api.admin.spend_reporting_dependencies import (
    _reporting_cache,
    _reporting_v2_enabled,
    _run_uncached_reporting_response,
)
from src.auth.roles import Permission
from src.billing.spend.spend_read import get_spend_read_source
from src.db.billing.workspace_request_logs import (
    WorkspaceLogFilters,
    WorkspaceLogScope,
    get_workspace_request_log,
    list_workspace_request_logs,
)
from src.db.runtime.reporting import ReportingTransaction, _run_reporting_transaction
from src.middleware.admin import require_any_admin_permission
from src.models.workspace_request_logs import (
    WorkspaceRequestLog,
    WorkspaceRequestLogPage,
    WorkspaceRequestLogPagination,
)
from src.services.reporting.request_log_cursor import (
    InvalidRequestLogCursor,
    decode_request_log_cursor,
    encode_request_log_cursor,
)
from src.services.reporting.workspace_request_logs import (
    InvalidWorkspaceLogWindow,
    is_key_hash,
    resolve_workspace_log_window,
    to_workspace_request_log,
)

router = APIRouter(tags=["Spend"])

# The scope comes from the session binding. These parameters would name a
# tenant, so the route refuses them instead of silently ignoring them.
_TENANT_QUERY_PARAMETERS = ("organization_id", "team_id", "user_id", "view")


def _workspace_scope(
    request: Request,
    authorization: str | None,
    x_master_key: str | None,
) -> WorkspaceLogScope:
    scope = get_auth_scope(
        request,
        authorization,
        x_master_key,
        required_permission=Permission.SPEND_READ_SELF,
    )
    workspace = scope.external_workspace
    if workspace is None or Permission.SPEND_READ_SELF not in scope.granted_permissions:
        raise HTTPException(
            status_code=403, detail="Workspace request logs require a customer workspace session"
        )
    if not _reporting_v2_enabled(request):
        raise HTTPException(
            status_code=403, detail="Usage reporting is not enabled for this account"
        )
    rejected = [name for name in _TENANT_QUERY_PARAMETERS if name in request.query_params]
    if rejected:
        raise HTTPException(
            status_code=422,
            detail=f"The workspace scope comes from the session; remove: {', '.join(rejected)}",
        )
    return WorkspaceLogScope(organization_id=workspace.organization_id, team_id=workspace.team_id)


@router.get(
    "/ui/api/workspace/logs",
    response_model=WorkspaceRequestLogPage,
    dependencies=[Depends(require_any_admin_permission((Permission.SPEND_READ_SELF,)))],
)
async def workspace_request_logs(
    request: Request,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    model: str | None = Query(default=None, min_length=1, max_length=512),
    api_key: str | None = Query(default=None, min_length=64, max_length=64),
    status: Literal["success", "error"] | None = Query(default=None),
    cursor: str | None = Query(default=None, max_length=1024),
    limit: int = Query(default=50, ge=1, le=100),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> WorkspaceRequestLogPage:
    started_at = perf_counter()
    scope = _workspace_scope(request, authorization, x_master_key)
    if api_key is not None and not is_key_hash(api_key):
        raise HTTPException(status_code=422, detail="api_key must be a key hash")
    try:
        window_start, window_end = resolve_workspace_log_window(
            start, end, now=datetime.now(tz=UTC)
        )
        after = decode_request_log_cursor(cursor) if cursor else None
    except InvalidRequestLogCursor as exc:
        raise HTTPException(status_code=422, detail="Invalid request log cursor") from exc
    except InvalidWorkspaceLogWindow as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filters = WorkspaceLogFilters(
        start=window_start,
        end=window_end,
        limit=limit,
        model=model,
        api_key=api_key,
        status=status,
        after=after,
    )
    db = db_or_503(request)
    cache = await _reporting_cache(request)
    source = get_spend_read_source()

    async def load() -> dict[str, object]:
        async def query(tx: ReportingTransaction, deadline: float, grace: float) -> dict[str, Any]:
            rows = await list_workspace_request_logs(
                tx,
                deadline=deadline,
                cancellation_grace=grace,
                source=source,
                scope=scope,
                filters=filters,
            )
            return {"rows": rows}

        return await _run_reporting_transaction(db, cache, query)

    result = await _run_uncached_reporting_response(cache=cache, loader=load)
    rows = cast(list[dict[str, object]], result["rows"])
    page = rows[:limit]
    has_more = len(rows) > limit
    next_cursor = (
        encode_request_log_cursor(page[-1].get("start_time"), page[-1].get("id"))
        if has_more and page
        else None
    )
    log_admin_query_timing(
        "workspace_request_logs",
        started_at,
        cursor=bool(cursor),
        limit=limit,
        filtered=bool(model or api_key or status),
    )
    return WorkspaceRequestLogPage(
        logs=[to_workspace_request_log(row) for row in page],
        pagination=WorkspaceRequestLogPagination(
            limit=limit, count=len(page), has_more=has_more, next_cursor=next_cursor
        ),
        start=window_start,
        end=window_end,
    )


@router.get(
    "/ui/api/workspace/logs/{log_id}",
    response_model=WorkspaceRequestLog,
    dependencies=[Depends(require_any_admin_permission((Permission.SPEND_READ_SELF,)))],
)
async def workspace_request_log(
    request: Request,
    log_id: str = Path(min_length=1, max_length=256),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> WorkspaceRequestLog:
    scope = _workspace_scope(request, authorization, x_master_key)
    db = db_or_503(request)
    cache = await _reporting_cache(request)
    source = get_spend_read_source()

    async def load() -> dict[str, object]:
        async def query(tx: ReportingTransaction, deadline: float, grace: float) -> dict[str, Any]:
            row = await get_workspace_request_log(
                tx,
                deadline=deadline,
                cancellation_grace=grace,
                source=source,
                scope=scope,
                log_id=log_id,
            )
            return {"row": row}

        return await _run_reporting_transaction(db, cache, query)

    result = await _run_uncached_reporting_response(cache=cache, loader=load)
    row = cast(dict[str, object] | None, result["row"])
    if row is None:
        # Rows of other workspaces and missing rows give the same answer.
        raise HTTPException(status_code=404, detail="Request log not found")
    return to_workspace_request_log(row)
