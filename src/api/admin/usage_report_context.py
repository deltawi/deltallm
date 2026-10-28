"""Shared request context and cached loading for the Usage and Dashboard reports.

Every report resolves the caller's reporting view, checks its filters and
dimensions against that view, and loads through the shared reporting cache, so
it gets the cache, the per-worker and global reporting limits, and the
reporting statement deadline.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException, Request

from src.api.admin.endpoints.common import (
    AuthScope,
    db_or_503,
    get_auth_scope,
    log_admin_query_timing,
    to_json_value,
)
from src.api.admin.spend_reporting_dependencies import (
    _load_reporting_response,
    _reporting_cache,
    _reporting_cache_revalidation_requested,
    _resolve_reporting_visibility,
)
from src.auth.roles import Permission
from src.billing.spend.spend_read import SpendReadSource, get_spend_read_source
from src.db.billing.spend_dimensions import legacy_cache_model_provider_overrides
from src.db.billing.usage_explorer_queries import ReportQuery
from src.db.runtime.reporting import _run_reporting_query
from src.services.reporting.spend_reporting_cache import SpendReportingCache
from src.services.reporting.spend_visibility import (
    SpendVisibility,
    user_identity_labels_visible,
)
from src.services.reporting.usage_filters import UsageFilterForbidden, UsageFilters

ReportView = Literal["organization", "team", "self"]
Rows = list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class UsageReportContext:
    scope: AuthScope
    visibility: SpendVisibility
    source: SpendReadSource
    cache: SpendReportingCache
    db: Any
    labels_visible: bool
    force_refresh: bool


async def usage_report_context(
    request: Request,
    *,
    view: ReportView | None,
    filters: UsageFilters,
    dimensions: tuple[str, ...],
    authorization: str | None,
    x_master_key: str | None,
    cache_control: str | None,
) -> UsageReportContext:
    scope = get_auth_scope(
        request, authorization, x_master_key, required_permission=Permission.SPEND_READ
    )
    visibility = _resolve_reporting_visibility(request, scope, view)
    try:
        filters.authorize(visibility)
    except UsageFilterForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not set(dimensions) <= set(visibility.allowed_groupings):
        raise HTTPException(
            status_code=403, detail="This usage dimension is outside your reporting scope"
        )
    return UsageReportContext(
        scope=scope,
        visibility=visibility,
        source=get_spend_read_source(),
        cache=await _reporting_cache(request),
        db=db_or_503(request),
        labels_visible=user_identity_labels_visible(scope, visibility),
        force_refresh=_reporting_cache_revalidation_requested(cache_control),
    )


def provider_overrides(request: Request) -> dict[str, str]:
    registry = getattr(request.app.state, "model_registry", {}) or {}
    return legacy_cache_model_provider_overrides(registry)


def report_cache_payload(
    endpoint: str, context: UsageReportContext, schema: int, **extra: Any
) -> dict[str, Any]:
    return {
        "endpoint": endpoint,
        "schema": schema,
        "source": context.source.table,
        "scope": context.visibility.cache_payload(),
        "user_identity_labels": context.labels_visible,
        **extra,
    }


async def load_usage_report(
    context: UsageReportContext,
    *,
    payload: dict[str, Any],
    cache_ttl: int,
    query: ReportQuery | None,
    shape: Callable[[Rows], dict[str, Any]],
    started_at: float,
    **log_context: Any,
) -> dict[str, Any]:
    """Load one report through the reporting cache. ``query=None`` shapes no rows."""

    async def loader() -> dict[str, Any]:
        if query is None:
            return shape([])
        rows = await _run_reporting_query(context.db, context.cache, query.sql, *query.params)
        return shape([to_json_value(dict(row)) for row in rows])

    result = await _load_reporting_response(
        cache=context.cache,
        cache_key=context.cache.key(payload),
        cache_ttl=cache_ttl,
        loader=loader,
        force_refresh=context.force_refresh,
    )
    log_admin_query_timing(
        f"usage_{str(payload['endpoint']).replace(':', '_')}",
        started_at,
        cache_status=result.status,
        force_refresh=context.force_refresh,
        **log_context,
    )
    return result.value
