"""Shared HTTP-edge reporting authorization, cache and failure mapping.

Legacy settings/auth/cache DTOs remain at the edge; SQL execution lives in db.reporting.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
from typing import TYPE_CHECKING, Literal

from fastapi import HTTPException, Request
from src.services.spend_reporting_cache import (
    ReportingLoadLimiter,
    ReportingQueryTimedOut,
    ReportingRefreshBusy,
    SpendReportingCache,
    SpendReportingCacheResult,
)
from src.services.spend_visibility import SpendVisibility, resolve_spend_visibility

if TYPE_CHECKING:
    from src.api.admin.endpoints.common import AuthScope

logger = logging.getLogger(__name__)


def _resolve_reporting_visibility(
    request: Request,
    scope: AuthScope,
    requested_view: Literal["organization", "team", "self"] | None,
) -> SpendVisibility:
    try:
        visibility = resolve_spend_visibility(
            scope,
            requested_view,
            scoped_views_enabled=_reporting_v2_enabled(request),
        )
        if not visibility.available_views:
            raise ValueError("Usage reporting is not enabled for this account")
        return visibility
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _reporting_v2_enabled(request: Request) -> bool:
    general_settings = getattr(
        getattr(request.app.state, "app_config", None), "general_settings", None
    )
    return bool(getattr(general_settings, "spend_reporting_v2_enabled", False))


def _reporting_context(request: Request, visibility: SpendVisibility) -> dict[str, object]:
    return {
        "api_version": 2 if _reporting_v2_enabled(request) else 1,
        "active_view": visibility.view,
    }


async def _reporting_cache(request: Request) -> SpendReportingCache:
    redis_client = getattr(request.app.state, "redis", None)
    general_settings = getattr(
        getattr(request.app.state, "app_config", None), "general_settings", None
    )
    max_concurrent_loads = int(getattr(general_settings, "spend_reporting_max_concurrency", 2))
    global_max_concurrent_loads = int(
        getattr(general_settings, "spend_reporting_global_max_concurrency", 2)
    )
    queue_timeout_seconds = float(
        getattr(general_settings, "spend_reporting_queue_timeout_seconds", 10.0)
    )
    execution_timeout_seconds = float(
        getattr(general_settings, "spend_reporting_execution_timeout_seconds", 60.0)
    )
    redis_timeout_seconds = float(
        getattr(general_settings, "spend_reporting_redis_timeout_seconds", 0.5)
    )

    guard = getattr(request.app.state, "spend_reporting_cache_guard", None)
    if not isinstance(guard, asyncio.Lock):
        guard = asyncio.Lock()
        request.app.state.spend_reporting_cache_guard = guard

    async with guard:
        existing = getattr(request.app.state, "spend_reporting_cache", None)
        if isinstance(existing, SpendReportingCache) and existing.redis is redis_client:
            await existing.reconfigure(
                max_concurrent_loads=max_concurrent_loads,
                global_max_concurrent_loads=global_max_concurrent_loads,
                load_queue_timeout_seconds=queue_timeout_seconds,
                load_execution_timeout_seconds=execution_timeout_seconds,
                redis_operation_timeout_seconds=redis_timeout_seconds,
            )
            return existing

        limiter = (
            existing.load_limiter
            if isinstance(existing, SpendReportingCache)
            else ReportingLoadLimiter(max_concurrent_loads)
        )
        await limiter.reconfigure(max_concurrent_loads)
        cache = SpendReportingCache(
            redis_client,
            max_concurrent_loads=max_concurrent_loads,
            global_max_concurrent_loads=global_max_concurrent_loads,
            load_queue_timeout_seconds=queue_timeout_seconds,
            load_execution_timeout_seconds=execution_timeout_seconds,
            redis_operation_timeout_seconds=redis_timeout_seconds,
            load_limiter=limiter,
        )
        request.app.state.spend_reporting_cache = cache
        return cache


def _reporting_cache_revalidation_requested(cache_control: str | None) -> bool:
    directives = {
        directive.strip().lower()
        for directive in str(cache_control or "").split(",")
        if directive.strip()
    }
    return "no-cache" in directives or "max-age=0" in directives


async def _load_reporting_response(
    *,
    cache: SpendReportingCache,
    cache_key: str,
    cache_ttl: int,
    loader: Callable[[], Awaitable[dict[str, object]]],
    force_refresh: bool,
) -> SpendReportingCacheResult:
    try:
        return await cache.get_or_load(
            cache_key,
            cache_ttl,
            loader,
            force_refresh=force_refresh,
        )
    except ReportingRefreshBusy as exc:
        raise HTTPException(
            status_code=503,
            detail="Usage reporting capacity is currently full. Please try again shortly.",
            headers={"Retry-After": "2"},
        ) from exc
    except ReportingQueryTimedOut as exc:
        logger.warning(
            "spend reporting request exceeded its execution deadline; timeout_seconds=%s",
            cache.load_execution_timeout_seconds,
        )
        raise HTTPException(
            status_code=503,
            detail="This usage report took too long to generate. Please try a shorter range or retry shortly.",
            headers={"Retry-After": "5"},
        ) from exc


async def _run_uncached_reporting_response(
    *,
    cache: SpendReportingCache,
    loader: Callable[[], Awaitable[dict[str, object]]],
) -> dict[str, object]:
    try:
        return await cache.run_uncached(loader)
    except ReportingRefreshBusy as exc:
        raise HTTPException(
            status_code=503,
            detail="Usage reporting capacity is currently full. Please try again shortly.",
            headers={"Retry-After": "2"},
        ) from exc
    except ReportingQueryTimedOut as exc:
        logger.warning(
            "spend reporting request exceeded its execution deadline; timeout_seconds=%s",
            cache.load_execution_timeout_seconds,
        )
        raise HTTPException(
            status_code=503,
            detail="This usage report took too long to generate. Please try a shorter range or retry shortly.",
            headers={"Retry-After": "5"},
        ) from exc
