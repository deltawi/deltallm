"""Live reports for the Admin UI Dashboard.

The Dashboard has no date picker. Each route reads one short, fixed window and
loads one Dashboard panel, so the UI can load the panels independently. Rolling
windows end at the current minute, which lets the shared reporting cache serve
every viewer in that minute from one database read.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request

from src.api.admin.spend_reporting_dependencies import _reporting_context
from src.api.admin.usage_report_context import (
    ReportView,
    Rows,
    UsageReportContext,
    load_usage_report,
    provider_overrides,
    report_cache_payload,
    usage_report_context,
)
from src.db.billing.live_usage_queries import (
    budget_alerts_query,
    live_daily_query,
    live_hourly_query,
    live_providers_query,
    live_top_query,
)
from src.middleware.admin import require_any_admin_permission
from src.services.reporting.spend_visibility import SPEND_VISIBILITY_PERMISSIONS
from src.services.reporting.usage_filters import UsageFilters

router = APIRouter(tags=["Spend"])

_SCHEMA_VERSION = 1
LIVE_TTL_SECONDS = 60
HISTORY_TTL_SECONDS = 900
HOURLY_BUCKETS = 48
_TOP_DIMENSIONS = ("api_key", "model", "team")
_PERMISSION = [Depends(require_any_admin_permission(SPEND_VISIBILITY_PERMISSIONS))]


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _minute(now: datetime) -> datetime:
    return now.replace(second=0, microsecond=0)


async def _context(
    request: Request,
    *,
    view: ReportView | None,
    authorization: str | None,
    x_master_key: str | None,
    cache_control: str | None,
) -> UsageReportContext:
    return await usage_report_context(
        request,
        view=view,
        filters=UsageFilters(),
        dimensions=(),
        authorization=authorization,
        x_master_key=x_master_key,
        cache_control=cache_control,
    )


def _envelope(
    request: Request, context: UsageReportContext, value: dict[str, Any]
) -> dict[str, Any]:
    return {**value, "reporting_context": _reporting_context(request, context.visibility)}


def hourly_payload(rows: Rows, *, first_bucket: datetime) -> dict[str, Any]:
    """Return one entry per hour, oldest first, with zeros for hours without traffic."""

    by_hour = {str(row.get("bucket"))[:13]: row for row in rows}
    buckets: list[dict[str, Any]] = []
    for index in range(HOURLY_BUCKETS):
        hour = first_bucket + timedelta(hours=index)
        row = by_hour.get(hour.strftime("%Y-%m-%dT%H")) or by_hour.get(hour.strftime("%Y-%m-%d %H"))
        buckets.append(
            {
                "bucket": hour.isoformat().replace("+00:00", "Z"),
                "total_spend": float((row or {}).get("total_spend") or 0),
                "request_count": int((row or {}).get("request_count") or 0),
                "failed_requests": int((row or {}).get("failed_requests") or 0),
            }
        )
    active_keys = int(rows[0].get("active_keys") or 0) if rows else 0
    return {"buckets": buckets, "active_keys_24h": active_keys}


@router.get("/ui/api/spend/live/hourly", dependencies=_PERMISSION)
async def live_usage_hourly(
    request: Request,
    view: ReportView | None = Query(default=None),
    cache_control: str | None = Header(default=None, alias="Cache-Control"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    """Hourly requests, failures and spend for the last 48 hours, and active keys in 24 hours."""

    started_at = perf_counter()
    context = await _context(
        request,
        view=view,
        authorization=authorization,
        x_master_key=x_master_key,
        cache_control=cache_control,
    )
    end = _minute(_now())
    first_bucket = end.replace(minute=0) - timedelta(hours=HOURLY_BUCKETS - 1)
    query = live_hourly_query(
        start=first_bucket,
        end=end + timedelta(minutes=1),
        active_keys_since=end - timedelta(hours=24),
        visibility=context.visibility,
        source=context.source,
    )
    value = await load_usage_report(
        context,
        payload=report_cache_payload(
            "live:hourly", context, _SCHEMA_VERSION, minute=end.isoformat()
        ),
        cache_ttl=LIVE_TTL_SECONDS,
        query=query,
        shape=lambda rows: hourly_payload(rows, first_bucket=first_bucket),
        started_at=started_at,
    )
    return _envelope(request, context, {**value, "as_of": end.isoformat()})


def daily_window(today: date) -> tuple[date, date]:
    """Complete UTC days from the first day of last month up to, not including, today."""

    first_of_month = today.replace(day=1)
    previous_month = (first_of_month - timedelta(days=1)).replace(day=1)
    return previous_month, today


@router.get("/ui/api/spend/live/daily", dependencies=_PERMISSION)
async def live_usage_daily(
    request: Request,
    view: ReportView | None = Query(default=None),
    cache_control: str | None = Header(default=None, alias="Cache-Control"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    """Daily totals for last month and this month, without today.

    Past days change rarely, so this report stays in the cache longer. The
    Dashboard adds today from the hourly report.
    """

    started_at = perf_counter()
    context = await _context(
        request,
        view=view,
        authorization=authorization,
        x_master_key=x_master_key,
        cache_control=cache_control,
    )
    start, end = daily_window(_now().date())
    query = live_daily_query(
        start=start, end=end, visibility=context.visibility, source=context.source
    )

    def shape(rows: Rows) -> dict[str, Any]:
        return {
            "days": [
                {
                    "date": str(row.get("bucket"))[:10],
                    "total_spend": float(row.get("total_spend") or 0),
                    "request_count": int(row.get("request_count") or 0),
                    "failed_requests": int(row.get("failed_requests") or 0),
                }
                for row in rows
            ]
        }

    value = await load_usage_report(
        context,
        payload=report_cache_payload(
            "live:daily", context, _SCHEMA_VERSION, start=start.isoformat(), end=end.isoformat()
        ),
        cache_ttl=HISTORY_TTL_SECONDS,
        query=query,
        shape=shape,
        started_at=started_at,
    )
    return _envelope(
        request,
        context,
        {**value, "start_date": start.isoformat(), "end_date": end.isoformat()},
    )


@router.get("/ui/api/spend/live/providers", dependencies=_PERMISSION)
async def live_usage_providers(
    request: Request,
    view: ReportView | None = Query(default=None),
    cache_control: str | None = Header(default=None, alias="Cache-Control"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    """Requests and failures per provider in the last 24 hours."""

    started_at = perf_counter()
    context = await _context(
        request,
        view=view,
        authorization=authorization,
        x_master_key=x_master_key,
        cache_control=cache_control,
    )
    end = _minute(_now()) + timedelta(minutes=1)
    overrides = provider_overrides(request)
    query = live_providers_query(
        start=end - timedelta(hours=24),
        end=end,
        visibility=context.visibility,
        source=context.source,
        model_provider_overrides=overrides,
    )

    def shape(rows: Rows) -> dict[str, Any]:
        return {
            "providers": [
                {
                    "provider": row.get("provider"),
                    "request_count": int(row.get("request_count") or 0),
                    "failed_requests": int(row.get("failed_requests") or 0),
                    "top_failing_model": row.get("top_failing_model"),
                    "top_failing_model_failures": int(row.get("top_failing_model_failures") or 0),
                    "top_error": row.get("top_error"),
                    "first_error_at": row.get("first_error_at"),
                    "last_error_at": row.get("last_error_at"),
                }
                for row in rows
            ]
        }

    value = await load_usage_report(
        context,
        payload=report_cache_payload(
            "live:providers",
            context,
            _SCHEMA_VERSION,
            minute=end.isoformat(),
            provider_overrides=overrides,
        ),
        cache_ttl=LIVE_TTL_SECONDS,
        query=query,
        shape=shape,
        started_at=started_at,
    )
    return _envelope(request, context, value)


@router.get("/ui/api/spend/live/top", dependencies=_PERMISSION)
async def live_usage_top(
    request: Request,
    view: ReportView | None = Query(default=None),
    cache_control: str | None = Header(default=None, alias="Cache-Control"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    """The busiest key, model and team in the last hour, as a share of requests."""

    started_at = perf_counter()
    context = await _context(
        request,
        view=view,
        authorization=authorization,
        x_master_key=x_master_key,
        cache_control=cache_control,
    )
    allowed = set(context.visibility.allowed_groupings)
    dimensions = tuple(name for name in _TOP_DIMENSIONS if name in allowed)
    end = _minute(_now()) + timedelta(minutes=1)
    query = (
        live_top_query(
            start=end - timedelta(hours=1),
            end=end,
            dimensions=dimensions,
            visibility=context.visibility,
            source=context.source,
        )
        if dimensions
        else None
    )

    def shape(rows: Rows) -> dict[str, Any]:
        total = int(rows[0].get("total_requests") or 0) if rows else 0
        return {
            "total_requests": total,
            "items": [
                {
                    "dimension": row.get("dimension"),
                    "group_key": row.get("group_key"),
                    "display_name": row.get("display_name"),
                    "request_count": int(row.get("request_count") or 0),
                    "share": (int(row.get("request_count") or 0) / total) if total else 0.0,
                }
                for row in rows
            ],
        }

    value = await load_usage_report(
        context,
        payload=report_cache_payload(
            "live:top", context, _SCHEMA_VERSION, minute=end.isoformat(), dimensions=dimensions
        ),
        cache_ttl=LIVE_TTL_SECONDS,
        query=query,
        shape=shape,
        started_at=started_at,
    )
    return _envelope(request, context, value)


@router.get("/ui/api/spend/live/budgets", dependencies=_PERMISSION)
async def live_budget_alerts(
    request: Request,
    view: ReportView | None = Query(default=None),
    cache_control: str | None = Header(default=None, alias="Cache-Control"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    """Organizations, teams and keys that used 80% or more of their max budget."""

    started_at = perf_counter()
    context = await _context(
        request,
        view=view,
        authorization=authorization,
        x_master_key=x_master_key,
        cache_control=cache_control,
    )
    end = _minute(_now())

    def shape(rows: Rows) -> dict[str, Any]:
        return {
            "items": [
                {
                    "kind": row.get("kind"),
                    "group_key": row.get("group_key"),
                    "display_name": row.get("display_name"),
                    "spend": float(row.get("spend") or 0),
                    "max_budget": float(row.get("max_budget") or 0),
                    "budget_duration": row.get("budget_duration"),
                    "budget_reset_at": row.get("budget_reset_at"),
                }
                for row in rows
            ]
        }

    value = await load_usage_report(
        context,
        payload=report_cache_payload(
            "live:budgets", context, _SCHEMA_VERSION, minute=end.isoformat()
        ),
        cache_ttl=LIVE_TTL_SECONDS,
        query=budget_alerts_query(visibility=context.visibility),
        shape=shape,
        started_at=started_at,
    )
    return _envelope(request, context, value)
