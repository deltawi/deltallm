from __future__ import annotations

import logging
from typing import Any

from src.db.cache_invalidation_outbox import CacheInvalidationOutboxRepository
from src.services.cache_invalidation import CacheInvalidationResult
from src.services.tier_admin_errors import TierAdminUnavailableError

logger = logging.getLogger(__name__)
_CACHE_INVALIDATION_SCHEDULED_REASON = "scheduled_for_worker"


async def enqueue_org_tier_assignment_cache_invalidation(
    tx: Any,
    *,
    organization_id: str,
    reason: str,
    metadata: dict[str, Any],
    max_attempts: int,
) -> CacheInvalidationResult:
    try:
        record = await CacheInvalidationOutboxRepository(tx).enqueue(
            scope_type="organization",
            scope_id=organization_id,
            reason=reason,
            metadata=metadata,
            max_attempts=max_attempts,
        )
    except Exception as exc:
        raise TierAdminUnavailableError("Cache invalidation could not be scheduled") from exc
    if record is None:
        raise TierAdminUnavailableError("Cache invalidation could not be scheduled")
    return CacheInvalidationResult(
        attempted=False,
        invalidated=False,
        queued=True,
        reason=_CACHE_INVALIDATION_SCHEDULED_REASON,
        invalidation_id=record.invalidation_id,
    )


async def apply_best_effort_org_cache_invalidation(
    scheduled: CacheInvalidationResult,
    *,
    cache_invalidation_service: Any | None,
    organization_id: str,
    reason: str,
) -> CacheInvalidationResult:
    if cache_invalidation_service is None:
        immediate = CacheInvalidationResult(
            attempted=False,
            invalidated=False,
            reason="cache_invalidation_service_unavailable",
        )
        return scheduled.with_immediate_result(immediate)

    invalidate_now = getattr(
        cache_invalidation_service,
        "invalidate_organization_cache_now",
        None,
    )
    if invalidate_now is None:
        immediate = CacheInvalidationResult(
            attempted=False,
            invalidated=False,
            reason="cache_invalidation_service_unavailable",
        )
        return scheduled.with_immediate_result(immediate)

    try:
        immediate = await invalidate_now(organization_id, reason=reason)
    except Exception as exc:
        logger.exception(
            "Best-effort tier assignment cache invalidation failed",
            extra={"organization_id": organization_id, "reason": reason},
        )
        immediate = CacheInvalidationResult(
            attempted=True,
            invalidated=False,
            reason="immediate_invalidation_failed",
            error_type=exc.__class__.__name__,
        )
    return scheduled.with_immediate_result(immediate)


__all__ = [
    "apply_best_effort_org_cache_invalidation",
    "enqueue_org_tier_assignment_cache_invalidation",
]
