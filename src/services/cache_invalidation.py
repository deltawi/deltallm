from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from src.db.cache_invalidation_outbox import CacheInvalidationOutboxRepository
from src.services.cache_invalidation_errors import CacheInvalidationBackendUnavailable
from src.services.cache_invalidation_worker import (
    CacheInvalidationWorker,
    CacheInvalidationWorkerConfig,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CacheInvalidationResult:
    attempted: bool
    invalidated: bool
    queued: bool = False
    count: int | None = None
    reason: str | None = None
    error_type: str | None = None
    enqueue_error_type: str | None = None
    invalidation_id: str | None = None
    latency_ms: int | None = None
    immediate_attempted: bool | None = None
    immediate_invalidated: bool | None = None
    immediate_count: int | None = None
    immediate_reason: str | None = None
    immediate_error_type: str | None = None
    immediate_latency_ms: int | None = None

    @property
    def safe(self) -> bool:
        return self.invalidated or self.queued

    def with_immediate_result(self, immediate: CacheInvalidationResult) -> CacheInvalidationResult:
        return CacheInvalidationResult(
            attempted=immediate.attempted,
            invalidated=immediate.invalidated,
            queued=self.queued,
            count=immediate.count,
            reason=self.reason,
            error_type=immediate.error_type,
            enqueue_error_type=self.enqueue_error_type,
            invalidation_id=self.invalidation_id,
            latency_ms=self.latency_ms,
            immediate_attempted=immediate.attempted,
            immediate_invalidated=immediate.invalidated,
            immediate_count=immediate.count,
            immediate_reason=immediate.reason,
            immediate_error_type=immediate.error_type,
            immediate_latency_ms=immediate.latency_ms,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "attempted": self.attempted,
            "invalidated": self.invalidated,
            "queued": self.queued,
        }
        optional_values = {
            "count": self.count,
            "reason": self.reason,
            "error_type": self.error_type,
            "enqueue_error_type": self.enqueue_error_type,
            "invalidation_id": self.invalidation_id,
            "latency_ms": self.latency_ms,
            "immediate_attempted": self.immediate_attempted,
            "immediate_invalidated": self.immediate_invalidated,
            "immediate_count": self.immediate_count,
            "immediate_reason": self.immediate_reason,
            "immediate_error_type": self.immediate_error_type,
            "immediate_latency_ms": self.immediate_latency_ms,
        }
        payload.update({key: value for key, value in optional_values.items() if value is not None})
        return payload


class CacheInvalidationService:
    def __init__(
        self,
        *,
        key_service: Any | None,
        repository: CacheInvalidationOutboxRepository | None,
        max_attempts: int = 10,
        immediate_timeout_seconds: float = 0.5,
    ) -> None:
        self.key_service = key_service
        self.repository = repository
        self.max_attempts = max(1, int(max_attempts))
        self.immediate_timeout_seconds = max(0.001, float(immediate_timeout_seconds))

    async def invalidate_organization(
        self,
        organization_id: str,
        *,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> CacheInvalidationResult:
        return await self._invalidate_scope(
            scope_type="organization",
            scope_id=organization_id,
            reason=reason,
            metadata=metadata,
            invalidate=self._invalidate_organization_now,
        )

    async def invalidate_organization_cache_now(
        self,
        organization_id: str,
        *,
        reason: str,
    ) -> CacheInvalidationResult:
        return await self._invalidate_scope_now(
            scope_type="organization",
            scope_id=organization_id,
            reason=reason,
            invalidate=self._invalidate_organization_now,
        )

    async def _invalidate_scope(
        self,
        *,
        scope_type: str,
        scope_id: str,
        reason: str,
        metadata: dict[str, Any] | None,
        invalidate: Callable[[str], Awaitable[int]],
    ) -> CacheInvalidationResult:
        started = perf_counter()
        if self.key_service is None:
            return await self._enqueue_after_failure(
                scope_type=scope_type,
                scope_id=scope_id,
                reason=reason,
                metadata=metadata,
                attempted=False,
                error_type=None,
                failure_reason="key_service_unavailable",
                started=started,
            )

        try:
            self._require_cache_invalidation_backend(scope_type)
            invalidated_count = await self._run_immediate_invalidation(invalidate, scope_id)
        except CacheInvalidationBackendUnavailable as exc:
            return await self._enqueue_after_failure(
                scope_type=scope_type,
                scope_id=scope_id,
                reason=reason,
                metadata=metadata,
                attempted=False,
                error_type=exc.__class__.__name__,
                failure_reason="cache_invalidation_backend_unavailable",
                started=started,
            )
        except TimeoutError as exc:
            logger.warning(
                "Timed out invalidating key auth cache",
                extra={"scope_type": scope_type, "scope_id": scope_id, "reason": reason},
            )
            return await self._enqueue_after_failure(
                scope_type=scope_type,
                scope_id=scope_id,
                reason=reason,
                metadata=metadata,
                attempted=True,
                error_type=exc.__class__.__name__,
                failure_reason="immediate_invalidation_timeout",
                started=started,
            )
        except Exception as exc:
            logger.exception(
                "Failed to invalidate key auth cache",
                extra={"scope_type": scope_type, "scope_id": scope_id, "reason": reason},
            )
            return await self._enqueue_after_failure(
                scope_type=scope_type,
                scope_id=scope_id,
                reason=reason,
                metadata=metadata,
                attempted=True,
                error_type=exc.__class__.__name__,
                failure_reason="immediate_invalidation_failed",
                started=started,
            )

        return CacheInvalidationResult(
            attempted=True,
            invalidated=True,
            queued=False,
            count=int(invalidated_count or 0),
            latency_ms=_elapsed_ms(started),
        )

    async def _invalidate_scope_now(
        self,
        *,
        scope_type: str,
        scope_id: str,
        reason: str,
        invalidate: Callable[[str], Awaitable[int]],
    ) -> CacheInvalidationResult:
        started = perf_counter()
        if self.key_service is None:
            return CacheInvalidationResult(
                attempted=False,
                invalidated=False,
                reason="key_service_unavailable",
                latency_ms=_elapsed_ms(started),
            )

        try:
            self._require_cache_invalidation_backend(scope_type)
            invalidated_count = await self._run_immediate_invalidation(invalidate, scope_id)
        except CacheInvalidationBackendUnavailable as exc:
            return CacheInvalidationResult(
                attempted=False,
                invalidated=False,
                reason="cache_invalidation_backend_unavailable",
                error_type=exc.__class__.__name__,
                latency_ms=_elapsed_ms(started),
            )
        except TimeoutError as exc:
            logger.warning(
                "Timed out invalidating key auth cache",
                extra={"scope_type": scope_type, "scope_id": scope_id, "reason": reason},
            )
            return CacheInvalidationResult(
                attempted=True,
                invalidated=False,
                reason="immediate_invalidation_timeout",
                error_type=exc.__class__.__name__,
                latency_ms=_elapsed_ms(started),
            )
        except Exception as exc:
            logger.exception(
                "Failed to invalidate key auth cache",
                extra={"scope_type": scope_type, "scope_id": scope_id, "reason": reason},
            )
            return CacheInvalidationResult(
                attempted=True,
                invalidated=False,
                reason="immediate_invalidation_failed",
                error_type=exc.__class__.__name__,
                latency_ms=_elapsed_ms(started),
            )

        return CacheInvalidationResult(
            attempted=True,
            invalidated=True,
            count=int(invalidated_count or 0),
            latency_ms=_elapsed_ms(started),
        )

    def _require_cache_invalidation_backend(self, scope_type: str) -> None:
        require_backend = getattr(self.key_service, "require_cache_invalidation_backend", None)
        if require_backend is not None:
            require_backend(scope_type=scope_type)

    async def _run_immediate_invalidation(
        self,
        invalidate: Callable[[str], Awaitable[int]],
        scope_id: str,
    ) -> int:
        return int(
            await asyncio.wait_for(
                invalidate(scope_id),
                timeout=self.immediate_timeout_seconds,
            )
            or 0
        )

    async def _enqueue_after_failure(
        self,
        *,
        scope_type: str,
        scope_id: str,
        reason: str,
        metadata: dict[str, Any] | None,
        attempted: bool,
        error_type: str | None,
        failure_reason: str,
        started: float,
    ) -> CacheInvalidationResult:
        if self.repository is None:
            return CacheInvalidationResult(
                attempted=attempted,
                invalidated=False,
                queued=False,
                reason=failure_reason,
                error_type=error_type,
                enqueue_error_type="repository_unavailable",
                latency_ms=_elapsed_ms(started),
            )

        try:
            record = await self.repository.enqueue(
                scope_type=scope_type,
                scope_id=scope_id,
                reason=reason,
                metadata=metadata,
                max_attempts=self.max_attempts,
            )
        except Exception as exc:
            logger.exception(
                "Failed to enqueue key auth cache invalidation",
                extra={"scope_type": scope_type, "scope_id": scope_id, "reason": reason},
            )
            return CacheInvalidationResult(
                attempted=attempted,
                invalidated=False,
                queued=False,
                reason=failure_reason,
                error_type=error_type,
                enqueue_error_type=exc.__class__.__name__,
                latency_ms=_elapsed_ms(started),
            )

        if record is None:
            return CacheInvalidationResult(
                attempted=attempted,
                invalidated=False,
                queued=False,
                reason=failure_reason,
                error_type=error_type,
                enqueue_error_type="enqueue_returned_none",
                latency_ms=_elapsed_ms(started),
            )

        return CacheInvalidationResult(
            attempted=attempted,
            invalidated=False,
            queued=True,
            reason=failure_reason,
            error_type=error_type,
            invalidation_id=record.invalidation_id,
            latency_ms=_elapsed_ms(started),
        )

    async def _invalidate_organization_now(self, organization_id: str) -> int:
        return int(await self.key_service.invalidate_keys_for_org(organization_id) or 0)


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


__all__ = [
    "CacheInvalidationResult",
    "CacheInvalidationService",
    "CacheInvalidationWorker",
    "CacheInvalidationWorkerConfig",
]
