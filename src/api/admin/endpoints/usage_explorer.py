"""Usage explorer reports for the Admin UI Usage page.

Each route loads one panel. The UI calls a route only when that panel is
visible, and every route goes through the shared reporting cache, the
per-worker and global reporting limits, and the reporting statement deadline.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from src.api.admin.spend_reporting_dependencies import _reporting_context
from src.api.admin.usage_report_context import (
    ReportView,
    UsageReportContext,
    load_usage_report,
    provider_overrides,
    report_cache_payload,
    usage_report_context,
)
from src.db.billing.spend_dimensions import SpendDimension
from src.db.billing.usage_explorer_queries import (
    BreakdownSort,
    ReportWindow,
    SeriesInterval,
    usage_breakdown_query,
    usage_series_query,
    usage_summary_query,
)
from src.middleware.admin import require_any_admin_permission
from src.services.reporting.spend_reporting_cache import reporting_cache_ttl
from src.services.reporting.spend_visibility import SPEND_VISIBILITY_PERMISSIONS
from src.services.reporting.usage_filters import UsageFilterError, UsageFilters

router = APIRouter(tags=["Spend"])

_SCHEMA_VERSION = 1
_DIMENSION_PATTERN = "^(model|provider|organization|team|user|api_key)$"


def report_window(start_date: date | None, end_date: date | None, today: date) -> ReportWindow:
    """Resolve UTC report dates to an inclusive window and its previous window."""

    effective_end = end_date or today
    end = datetime.combine(effective_end, time.max, tzinfo=UTC)
    if start_date is None:
        return ReportWindow(start=None, end=end)
    if start_date > effective_end:
        raise HTTPException(status_code=422, detail="start_date must not be after end_date")
    start = datetime.combine(start_date, time.min, tzinfo=UTC)
    length = timedelta(days=(effective_end - start_date).days + 1)
    try:
        previous_start = start - length
    except OverflowError as exc:
        raise HTTPException(status_code=422, detail="start_date is too early") from exc
    return ReportWindow(start=start, end=end, previous_start=previous_start)


def usage_filters_query(
    model: str | None = Query(default=None, max_length=512),
    provider: str | None = Query(default=None, max_length=512),
    organization: str | None = Query(default=None, max_length=512),
    team: str | None = Query(default=None, max_length=512),
    user: str | None = Query(default=None, max_length=512),
    api_key: str | None = Query(default=None, max_length=512),
    unassigned: list[str] | None = Query(default=None, max_length=6),
) -> UsageFilters:
    try:
        return UsageFilters.build(
            values={
                "model": model,
                "provider": provider,
                "organization": organization,
                "team": team,
                "user": user,
                "api_key": api_key,
            },
            unassigned=unassigned,
        )
    except UsageFilterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _provider_overrides(
    request: Request, filters: UsageFilters, dimensions: tuple[SpendDimension, ...]
) -> dict[str, str] | None:
    if "provider" not in filters.dimensions and "provider" not in dimensions:
        return None
    return provider_overrides(request)


def _cache_payload(
    endpoint: str,
    context: UsageReportContext,
    filters: UsageFilters,
    overrides: dict[str, str] | None,
    **extra: Any,
) -> dict[str, Any]:
    return report_cache_payload(
        f"explorer:{endpoint}",
        context,
        _SCHEMA_VERSION,
        filters=filters.cache_payload(),
        provider_overrides=overrides,
        **{
            key: value.isoformat() if isinstance(value, date) else value
            for key, value in extra.items()
        },
    )


def _today() -> date:
    return datetime.now(tz=UTC).date()


def _metrics(row: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    return {
        "total_spend": float(row.get(f"{prefix}total_spend") or 0),
        "total_tokens": int(row.get(f"{prefix}total_tokens") or 0),
        "prompt_tokens": int(row.get(f"{prefix}prompt_tokens") or 0),
        "completion_tokens": int(row.get(f"{prefix}completion_tokens") or 0),
        "request_count": int(row.get(f"{prefix}request_count") or 0),
        "failed_requests": int(row.get(f"{prefix}failed_requests") or 0),
    }


@router.get(
    "/ui/api/spend/explorer/summary",
    dependencies=[Depends(require_any_admin_permission(SPEND_VISIBILITY_PERMISSIONS))],
)
async def usage_explorer_summary(
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    view: ReportView | None = Query(default=None),
    filters: UsageFilters = Depends(usage_filters_query),
    cache_control: str | None = Header(default=None, alias="Cache-Control"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    started_at = perf_counter()
    context = await usage_report_context(
        request,
        view=view,
        filters=filters,
        dimensions=(),
        authorization=authorization,
        x_master_key=x_master_key,
        cache_control=cache_control,
    )
    window = report_window(start_date, end_date, _today())
    overrides = _provider_overrides(request, filters, ())
    query = usage_summary_query(
        window=window,
        visibility=context.visibility,
        filters=filters,
        source=context.source,
        model_provider_overrides=overrides,
    )

    def shape(rows: list[dict[str, Any]]) -> dict[str, Any]:
        row = rows[0] if rows else {}
        return {
            "current": _metrics(row),
            "previous": _metrics(row, "previous_") if window.compares else None,
        }

    value = await load_usage_report(
        context,
        payload=_cache_payload(
            "summary",
            context,
            filters,
            overrides,
            start_date=start_date,
            end_date=end_date,
            window=_window_payload(window),
        ),
        cache_ttl=reporting_cache_ttl(start_date, end_date),
        query=query,
        shape=shape,
        started_at=started_at,
    )
    return {
        **value,
        "window": _window_payload(window),
        "reporting_context": _reporting_context(request, context.visibility),
    }


def _window_payload(window: ReportWindow) -> dict[str, str | None]:
    previous_end = (
        (window.start - timedelta(days=1)).date().isoformat()
        if window.compares and window.start
        else None
    )
    return {
        "start_date": window.start.date().isoformat() if window.start else None,
        "end_date": window.end.date().isoformat(),
        "previous_start_date": (
            window.previous_start.date().isoformat() if window.previous_start else None
        ),
        "previous_end_date": previous_end,
    }


@router.get(
    "/ui/api/spend/explorer/series",
    dependencies=[Depends(require_any_admin_permission(SPEND_VISIBILITY_PERMISSIONS))],
)
async def usage_explorer_series(
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    interval: SeriesInterval = Query(default="day"),
    split_by: str | None = Query(default=None, pattern=_DIMENSION_PATTERN),
    max_groups: int = Query(default=5, ge=1, le=8),
    view: ReportView | None = Query(default=None),
    filters: UsageFilters = Depends(usage_filters_query),
    cache_control: str | None = Header(default=None, alias="Cache-Control"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    started_at = perf_counter()
    dimension: SpendDimension | None = split_by  # type: ignore[assignment]
    dimensions: tuple[SpendDimension, ...] = (dimension,) if dimension else ()
    context = await usage_report_context(
        request,
        view=view,
        filters=filters,
        dimensions=dimensions,
        authorization=authorization,
        x_master_key=x_master_key,
        cache_control=cache_control,
    )
    window = report_window(start_date, end_date, _today())
    overrides = _provider_overrides(request, filters, dimensions)
    query = usage_series_query(
        window=window,
        interval=interval,
        split_by=dimension,
        max_groups=max_groups,
        visibility=context.visibility,
        filters=filters,
        source=context.source,
        model_provider_overrides=overrides,
        user_identity_labels_visible=context.labels_visible,
    )
    value = await load_usage_report(
        context,
        payload=_cache_payload(
            "series",
            context,
            filters,
            overrides,
            start_date=start_date,
            end_date=end_date,
            window=_window_payload(window),
            interval=interval,
            split_by=dimension,
            max_groups=max_groups,
        ),
        cache_ttl=reporting_cache_ttl(start_date, end_date),
        query=query,
        shape=series_payload,
        started_at=started_at,
        split_by=dimension,
    )
    return {
        "interval": interval,
        "split_by": dimension,
        **value,
        "window": _window_payload(window),
        "reporting_context": _reporting_context(request, context.visibility),
    }


def series_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Separate ranked group identities from per-bucket values."""

    groups: dict[int, dict[str, Any]] = {}
    buckets: list[dict[str, Any]] = []
    for row in rows:
        index = int(row.get("series_index") or 0)
        bucket = row.get("bucket")
        values = _metrics(row)
        values.pop("prompt_tokens")
        values.pop("completion_tokens")
        buckets.append({"bucket": str(bucket), "series_index": index, **values})
        group = groups.setdefault(
            index,
            {
                "series_index": index,
                "group_key": row.get("group_key"),
                "display_name": row.get("display_name"),
                "is_unassigned": bool(row.get("is_unassigned")),
                "is_other": index == 0,
                "total_spend": 0.0,
                "total_tokens": 0,
                "request_count": 0,
                "failed_requests": 0,
            },
        )
        for metric in ("total_spend", "total_tokens", "request_count", "failed_requests"):
            group[metric] += values[metric]
    ordered = sorted(groups.values(), key=lambda group: (group["is_other"], group["series_index"]))
    return {"groups": ordered, "buckets": buckets}


@router.get(
    "/ui/api/spend/explorer/breakdown",
    dependencies=[Depends(require_any_admin_permission(SPEND_VISIBILITY_PERMISSIONS))],
)
async def usage_explorer_breakdown(
    request: Request,
    group_by: str = Query(pattern=_DIMENSION_PATTERN),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    sort_by: BreakdownSort = Query(default="spend"),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10_000),
    view: ReportView | None = Query(default=None),
    filters: UsageFilters = Depends(usage_filters_query),
    cache_control: str | None = Header(default=None, alias="Cache-Control"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    started_at = perf_counter()
    dimension: SpendDimension = group_by  # type: ignore[assignment]
    context = await usage_report_context(
        request,
        view=view,
        filters=filters,
        dimensions=(dimension,),
        authorization=authorization,
        x_master_key=x_master_key,
        cache_control=cache_control,
    )
    window = report_window(start_date, end_date, _today())
    overrides = _provider_overrides(request, filters, (dimension,))
    normalized_search = search.strip() if search and search.strip() else None
    query = usage_breakdown_query(
        window=window,
        group_by=dimension,
        sort_by=sort_by,
        search=normalized_search,
        limit=limit,
        offset=offset,
        visibility=context.visibility,
        filters=filters,
        source=context.source,
        model_provider_overrides=overrides,
        user_identity_labels_visible=context.labels_visible,
    )

    def shape(rows: list[dict[str, Any]]) -> dict[str, Any]:
        head = rows[0] if rows else {}
        total = int(head.get("total_count") or 0)
        data = [
            {
                "group_key": row.get("group_key"),
                "is_unassigned": bool(row.get("is_unassigned")),
                "display_name": row.get("display_name"),
                **_metrics(row),
                "previous_spend": (
                    float(row["previous_spend"]) if row.get("previous_spend") is not None else None
                ),
            }
            # A page past the end returns one totals row with no group.
            for row in rows
            if row.get("request_count") is not None
        ]
        return {
            "data": data,
            "scope_spend": float(head.get("scope_spend") or 0),
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
            },
        }

    value = await load_usage_report(
        context,
        payload=_cache_payload(
            "breakdown",
            context,
            filters,
            overrides,
            group_by=dimension,
            start_date=start_date,
            end_date=end_date,
            window=_window_payload(window),
            sort_by=sort_by,
            search=normalized_search,
            limit=limit,
            offset=offset,
        ),
        cache_ttl=reporting_cache_ttl(start_date, end_date),
        query=query,
        shape=shape,
        started_at=started_at,
        group_by=dimension,
    )
    response: dict[str, Any] = {
        "group_by": dimension,
        **value,
        "window": _window_payload(window),
        "reporting_context": _reporting_context(request, context.visibility),
    }
    if dimension == "user":
        response["capabilities"] = {"user_identity_labels": context.labels_visible}
    return response
