from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class RouteGroupCache(Protocol):
    async def invalidate(self) -> bool | None: ...


class RouteGroupReloader(Protocol):
    async def reload_route_groups(self) -> None: ...


class RouteGroupInvalidationPublisher(Protocol):
    redis: object | None

    async def notify(self, *targets: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class RouteGroupRefreshResult:
    warnings: tuple[str, ...] = ()


async def refresh_route_group_runtime(
    *,
    cache: RouteGroupCache | None,
    reloader: RouteGroupReloader | None,
    invalidation: RouteGroupInvalidationPublisher | None,
) -> RouteGroupRefreshResult:
    """Refresh committed route-group state without reclassifying the mutation."""

    warnings: list[str] = []
    # The reloader owns local invalidation so cache fencing and generation build
    # are one operation. A cache-only fallback remains for reduced configurations.
    if reloader is None and cache is not None:
        try:
            invalidated = await cache.invalidate()
        except Exception:
            invalidated = False
            logger.warning("route-group cache invalidation failed after commit", exc_info=True)
        if invalidated is False:
            warnings.append("Mutation committed, but shared route-group cache invalidation failed")

    if reloader is not None:
        try:
            await reloader.reload_route_groups()
        except Exception:
            warnings.append("Mutation committed, but local route-group runtime refresh failed")
            logger.warning("local route-group runtime refresh failed after commit", exc_info=True)

    if invalidation is not None and invalidation.redis is not None:
        try:
            notified = await invalidation.notify("prompt", "route_groups")
        except Exception:
            notified = False
            logger.warning(
                "route-group invalidation publication failed after commit", exc_info=True
            )
        if not notified:
            warnings.append("Mutation committed, but cross-replica route-group invalidation failed")

    return RouteGroupRefreshResult(warnings=tuple(warnings))
