"""Usage report and request log exports (CSV, JSON and JSONL downloads).

Each export applies the same reporting view, filters and dimension checks as the
on-screen report or request list, runs one bounded query under the reporting
limits and statement deadline, and writes a required audit event before it
returns the file. Exports never include request metadata or message content.
"""

from __future__ import annotations

from datetime import date
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from src.api.admin.endpoints.common import (
    AuthScope,
    db_or_503,
    get_auth_scope,
    log_admin_query_timing,
    to_json_value,
)
from src.api.admin.endpoints.usage_explorer import (
    report_window,
    today_utc,
    usage_filters_query,
    window_payload,
)
from src.api.admin.request_log_scope import (
    RequestLogParams,
    decode_cursor_or_422,
    detail_filter_names,
    request_log_params,
    resolve_request_log_scope,
)
from src.api.admin.spend_reporting_dependencies import (
    _reporting_cache,
    _run_uncached_reporting_response,
)
from src.api.admin.usage_report_context import (
    ReportView,
    provider_overrides,
    usage_report_context,
)
from src.api.audit import emit_control_audit_event
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.billing.spend.spend_read import get_spend_read_source
from src.db.billing.admin_request_logs import request_log_queries
from src.db.billing.spend_dimensions import SpendDimension
from src.db.billing.usage_explorer_queries import ExportInterval, usage_export_query
from src.db.runtime.reporting import _run_reporting_query
from src.middleware.admin import require_any_admin_permission
from src.services.reporting.request_log_cursor import encode_request_log_cursor
from src.services.reporting.spend_visibility import SPEND_VISIBILITY_PERMISSIONS
from src.services.reporting.usage_export import (
    DEFAULT_EXPORT_ROWS,
    MAX_EXPORT_ROWS,
    ExportFile,
    LogExportFormat,
    ReportExportFormat,
    build_report_export,
    build_request_log_export,
    export_filename,
)
from src.services.reporting.usage_filters import UsageFilters

router = APIRouter(tags=["Spend"])

_DIMENSION_PATTERN = "^(model|provider|organization|team|user|api_key)$"


def _file_response(export: ExportFile, *, next_cursor: str | None = None) -> Response:
    headers = {
        "Content-Disposition": f'attachment; filename="{export.filename}"',
        "Cache-Control": "no-store",
        "X-DeltaLLM-Export-Rows": str(export.rows),
        "X-DeltaLLM-Export-Truncated": "true" if export.truncated else "false",
    }
    if next_cursor:
        headers["X-DeltaLLM-Export-Next-Cursor"] = next_cursor
    return Response(content=export.content, media_type=export.media_type, headers=headers)


async def _audit_export(
    request: Request,
    *,
    action: AuditAction,
    scope: AuthScope,
    started_at: float,
    resource_type: str,
    metadata: dict[str, Any],
) -> None:
    # Required delivery: an export must not leave the gateway without an audit record.
    await emit_control_audit_event(
        request=request,
        request_start=started_at,
        action=action,
        status="success",
        resource_type=resource_type,
        scope=scope,
        metadata=metadata,
        critical=True,
    )


@router.get(
    "/ui/api/spend/explorer/export",
    dependencies=[Depends(require_any_admin_permission(SPEND_VISIBILITY_PERMISSIONS))],
)
async def export_usage_report(
    request: Request,
    group_by: str = Query(pattern=_DIMENSION_PATTERN),
    format: ReportExportFormat = Query(default="csv"),
    interval: ExportInterval | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    limit: int = Query(default=DEFAULT_EXPORT_ROWS, ge=1, le=MAX_EXPORT_ROWS),
    view: ReportView | None = Query(default=None),
    filters: UsageFilters = Depends(usage_filters_query),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> Response:
    started_at = perf_counter()
    dimension: SpendDimension = group_by  # type: ignore[assignment]
    context = await usage_report_context(
        request,
        view=view,
        filters=filters,
        dimensions=(dimension,),
        authorization=authorization,
        x_master_key=x_master_key,
        cache_control=None,
    )
    window = report_window(start_date, end_date, today_utc())
    overrides = (
        provider_overrides(request)
        if dimension == "provider" or "provider" in filters.dimensions
        else None
    )
    query = usage_export_query(
        window=window,
        group_by=dimension,
        interval=interval,
        max_rows=limit + 1,
        visibility=context.visibility,
        filters=filters,
        source=context.source,
        model_provider_overrides=overrides,
        user_identity_labels_visible=context.labels_visible,
    )

    async def load() -> dict[str, Any]:
        rows = await _run_reporting_query(context.db, context.cache, query.sql, *query.params)
        return {"rows": [to_json_value(dict(row)) for row in rows]}

    loaded = await _run_uncached_reporting_response(cache=context.cache, loader=load)
    period = window_payload(window)
    stem = f"usage-{dimension}" + (f"-by-{interval}" if interval else "")
    export = build_report_export(
        rows=loaded["rows"],
        dimension=dimension,
        interval=interval,
        max_rows=limit,
        export_format=format,
        window=period,
        filename=export_filename(stem, start_date, window.end.date(), format),
    )
    await _audit_export(
        request,
        action=AuditAction.SPEND_REPORT_EXPORT,
        scope=context.scope,
        started_at=started_at,
        resource_type="usage_report",
        metadata={
            "format": format,
            "group_by": dimension,
            "interval": interval,
            "start_date": period["start_date"],
            "end_date": period["end_date"],
            "view": context.visibility.view,
            "filters": sorted(filters.dimensions),
            "rows": export.rows,
            "truncated": export.truncated,
        },
    )
    log_admin_query_timing(
        "usage_export",
        started_at,
        group_by=dimension,
        interval=interval,
        rows=export.rows,
        truncated=export.truncated,
    )
    return _file_response(export)


@router.get(
    "/ui/api/logs/export",
    dependencies=[Depends(require_any_admin_permission(SPEND_VISIBILITY_PERMISSIONS))],
)
async def export_request_logs(
    request: Request,
    params: RequestLogParams = Depends(request_log_params),
    format: LogExportFormat = Query(default="csv"),
    limit: int = Query(default=DEFAULT_EXPORT_ROWS, ge=1, le=MAX_EXPORT_ROWS),
    cursor: str | None = Query(default=None, max_length=1024),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> Response:
    started_at = perf_counter()
    scope = get_auth_scope(
        request, authorization, x_master_key, required_permission=Permission.SPEND_READ
    )
    log_scope = resolve_request_log_scope(request, scope, params)
    decoded_cursor = decode_cursor_or_422(cursor) if cursor else None
    db = db_or_503(request)
    cache = await _reporting_cache(request)
    queries = request_log_queries(
        filters=log_scope.filters,
        visibility=log_scope.visibility,
        source=get_spend_read_source(),
        limit=limit + 1,
        offset=0,
        cursor=decoded_cursor,
        model_provider_overrides=log_scope.model_provider_overrides,
        user_identity_labels_visible=log_scope.user_identity_labels_visible,
        include_details=False,
    )

    async def load() -> dict[str, Any]:
        rows = await _run_reporting_query(db, cache, queries.page.sql, *queries.page.params)
        return {"rows": [dict(row) for row in rows]}

    loaded = await _run_uncached_reporting_response(cache=cache, loader=load)
    rows: list[dict[str, Any]] = loaded["rows"]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_request_log_cursor(last.get("start_time"), last.get("id"))
    filters = log_scope.filters
    export = build_request_log_export(
        rows=[to_json_value(row) for row in rows],
        max_rows=limit,
        export_format=format,
        filename=export_filename(
            "requests",
            filters.start.date() if filters.start else None,
            filters.end.date() if filters.end else None,
            format,
        ),
    )
    await _audit_export(
        request,
        action=AuditAction.SPEND_LOGS_EXPORT,
        scope=scope,
        started_at=started_at,
        resource_type="request_logs",
        metadata={
            "format": format,
            "since": filters.start.isoformat() if filters.start else None,
            "until": filters.end.isoformat() if filters.end else None,
            "view": log_scope.visibility.view,
            "filters": sorted(filters.usage.dimensions) + detail_filter_names(filters),
            "status": filters.status,
            "cache_hit": filters.cache_hit,
            "request_id": bool(filters.request_id),
            "cursor": bool(cursor),
            "rows": export.rows,
            "truncated": export.truncated,
        },
    )
    log_admin_query_timing(
        "request_logs_export",
        started_at,
        rows=export.rows,
        truncated=export.truncated,
        scoped=not scope.is_platform_admin,
    )
    return _file_response(export, next_cursor=next_cursor)
