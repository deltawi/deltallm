"""Query parameters and reporting scope shared by the request log list and its export.

Both routes read the same filters and apply the same visibility rules, so an
export can never return a row that the on-screen list would hide.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from fastapi import HTTPException, Query, Request

from src.api.admin.endpoints.common import AuthScope
from src.api.admin.spend_reporting_dependencies import _resolve_reporting_visibility
from src.db.billing.admin_request_logs import RequestLogFilters, RequestLogStatus
from src.db.billing.spend_dimensions import legacy_cache_model_provider_overrides
from src.services.reporting.request_log_cursor import (
    InvalidRequestLogCursor,
    decode_request_log_cursor,
)
from src.services.reporting.spend_visibility import (
    SpendVisibility,
    user_identity_labels_visible,
)
from src.services.reporting.usage_filters import (
    UsageFilterError,
    UsageFilterForbidden,
    UsageFilters,
)


@dataclass(frozen=True, slots=True)
class RequestLogParams:
    model: str | None
    provider: str | None
    organization_id: str | None
    team_id: str | None
    user_id: str | None
    api_key: str | None
    status: RequestLogStatus | None
    cache_hit: bool | None
    request_id: str | None
    tag: str | None
    end_user: str | None
    call_type: str | None
    deployment: str | None
    http_status: int | None
    error_type: str | None
    min_spend: float | None
    min_duration_ms: int | None
    unassigned: list[str] | None
    since: datetime | None
    until: datetime | None
    start_date: date | None
    end_date: date | None
    view: Literal["organization", "team", "self"] | None


def request_log_params(
    model: str | None = Query(default=None, max_length=512),
    provider: str | None = Query(default=None, max_length=512),
    organization_id: str | None = Query(default=None, max_length=512),
    team_id: str | None = Query(default=None, max_length=512),
    user_id: str | None = Query(default=None, max_length=512),
    api_key: str | None = Query(default=None, max_length=512),
    status: RequestLogStatus | None = Query(default=None),
    cache_hit: bool | None = Query(default=None),
    request_id: str | None = Query(default=None, max_length=256),
    tag: str | None = Query(default=None, max_length=256),
    end_user: str | None = Query(default=None, max_length=512),
    call_type: str | None = Query(default=None, max_length=128),
    deployment: str | None = Query(default=None, max_length=512),
    http_status: int | None = Query(default=None, ge=100, le=599),
    error_type: str | None = Query(default=None, max_length=256),
    min_spend: float | None = Query(default=None, ge=0, le=1_000_000_000),
    min_duration_ms: int | None = Query(default=None, ge=0, le=86_400_000),
    unassigned: list[str] | None = Query(default=None, max_length=6),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    view: Literal["organization", "team", "self"] | None = Query(default=None),
) -> RequestLogParams:
    return RequestLogParams(
        model=model,
        provider=provider,
        organization_id=organization_id,
        team_id=team_id,
        user_id=user_id,
        api_key=api_key,
        status=status,
        cache_hit=cache_hit,
        request_id=request_id,
        tag=tag,
        end_user=end_user,
        call_type=call_type,
        deployment=deployment,
        http_status=http_status,
        error_type=error_type,
        min_spend=min_spend,
        min_duration_ms=min_duration_ms,
        unassigned=unassigned,
        since=since,
        until=until,
        start_date=start_date,
        end_date=end_date,
        view=view,
    )


@dataclass(frozen=True, slots=True)
class RequestLogScope:
    visibility: SpendVisibility
    filters: RequestLogFilters
    model_provider_overrides: dict[str, str] | None
    user_identity_labels_visible: bool


_DETAIL_FILTERS = (
    "tag",
    "end_user",
    "call_type",
    "deployment",
    "http_status",
    "error_type",
    "min_spend",
    "min_duration_ms",
)


def detail_filter_names(filters: RequestLogFilters) -> list[str]:
    """Names of the detail filters in use, for logs and audit metadata (never their values)."""

    return [name for name in _DETAIL_FILTERS if getattr(filters, name) is not None]


def log_window_start(start_date: date | None, since: datetime | None) -> datetime | None:
    """Return the later of the start date and ``since`` (a naive ``since`` is UTC)."""

    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    day_start = datetime.combine(start_date, time.min, tzinfo=UTC) if start_date else None
    candidates = [value for value in (day_start, since) if value is not None]
    return max(candidates) if candidates else None


def log_window_end(end_date: date | None, until: datetime | None) -> datetime | None:
    """Return the earlier of the end date and ``until`` (exclusive; a naive ``until`` is UTC)."""

    if until is not None:
        if until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        try:
            until -= timedelta(microseconds=1)
        except OverflowError as exc:
            raise HTTPException(status_code=422, detail="until is too early") from exc
    day_end = datetime.combine(end_date, time.max, tzinfo=UTC) if end_date else None
    candidates = [value for value in (day_end, until) if value is not None]
    return min(candidates) if candidates else None


def _optional_text(value: str | None) -> str | None:
    stripped = value.strip() if value else ""
    return stripped or None


def decode_cursor_or_422(value: str) -> tuple[datetime, str]:
    try:
        return decode_request_log_cursor(value)
    except InvalidRequestLogCursor as exc:
        raise HTTPException(status_code=422, detail="Invalid request log cursor") from exc


def resolve_request_log_scope(
    request: Request, scope: AuthScope, params: RequestLogParams
) -> RequestLogScope:
    """Check the caller's view and filters, and resolve the time window (all before any query)."""

    visibility = _resolve_reporting_visibility(request, scope, params.view)
    if not visibility.can_view_request_logs:
        raise HTTPException(status_code=403, detail="Request logs are outside your reporting scope")
    try:
        usage_filters = UsageFilters.build(
            values={
                "model": params.model,
                "provider": params.provider,
                "organization": params.organization_id,
                "team": params.team_id,
                "user": params.user_id,
                "api_key": params.api_key,
            },
            unassigned=params.unassigned,
        )
        usage_filters.authorize(visibility)
    except UsageFilterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UsageFilterForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    window_start = log_window_start(params.start_date, params.since)
    window_end = log_window_end(params.end_date, params.until)
    if window_start is not None and window_end is not None and window_start > window_end:
        raise HTTPException(status_code=422, detail="since must be before until")
    overrides = None
    if params.provider:
        registry = getattr(request.app.state, "model_registry", {}) or {}
        overrides = legacy_cache_model_provider_overrides(registry)
    return RequestLogScope(
        visibility=visibility,
        filters=RequestLogFilters(
            start=window_start,
            end=window_end,
            usage=usage_filters,
            status=params.status,
            cache_hit=params.cache_hit,
            request_id=_optional_text(params.request_id),
            tag=_optional_text(params.tag),
            end_user=_optional_text(params.end_user),
            call_type=_optional_text(params.call_type),
            deployment=_optional_text(params.deployment),
            http_status=params.http_status,
            error_type=_optional_text(params.error_type),
            min_spend=params.min_spend,
            min_duration_ms=params.min_duration_ms,
        ),
        model_provider_overrides=overrides,
        user_identity_labels_visible=user_identity_labels_visible(scope, visibility),
    )
