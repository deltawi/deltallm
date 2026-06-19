from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from src.db.cache_invalidation_outbox import (
    CacheInvalidationOutboxRecord,
    CacheInvalidationOutboxRepository,
)
from src.services.cache_invalidation_errors import CacheInvalidationBackendUnavailable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CacheInvalidationWorkerConfig:
    poll_interval_seconds: float = 5.0
    max_batch_size: int = 25
    max_concurrency: int = 4
    lease_seconds: int = 60
    record_timeout_seconds: float = 10.0
    max_attempts: int = 10
    retry_initial_seconds: int = 5
    retry_max_seconds: int = 300


class CacheInvalidationWorker:
    def __init__(
        self,
        *,
        repository: CacheInvalidationOutboxRepository,
        key_service: Any,
        worker_id: str,
        config: CacheInvalidationWorkerConfig | None = None,
    ) -> None:
        self.repository = repository
        self.key_service = key_service
        self.worker_id = str(worker_id or "").strip() or "cache-invalidation-worker"
        self.config = config or CacheInvalidationWorkerConfig()
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    async def run(self) -> None:
        while not self._stopped:
            try:
                processed = await self.process_once()
            except Exception:
                logger.exception(
                    "cache invalidation worker cycle failed",
                    extra={"worker_id": self.worker_id},
                )
                processed = 0
            if processed == 0:
                await asyncio.sleep(self.config.poll_interval_seconds)

    async def process_once(self) -> int:
        records = await self.repository.claim_due(
            worker_id=self.worker_id,
            lease_seconds=self.config.lease_seconds,
            limit=self.config.max_batch_size,
        )
        if not records:
            return 0

        semaphore = asyncio.Semaphore(max(1, min(self.config.max_concurrency, len(records))))

        async def _run(record: CacheInvalidationOutboxRecord) -> None:
            async with semaphore:
                await self._process_record(record)

        await asyncio.gather(*[_run(record) for record in records])
        return len(records)

    async def _process_record(self, record: CacheInvalidationOutboxRecord) -> None:
        try:
            await self._invalidate_record_with_timeout(record)
            completed = await self.repository.mark_completed(
                record.invalidation_id,
                worker_id=self.worker_id,
            )
            if not completed:
                self._log_transition_skipped(record, transition="completed")
                return
            logger.info(
                "cache invalidation outbox record completed",
                extra={
                    "invalidation_id": record.invalidation_id,
                    "scope_type": record.scope_type,
                    "scope_id": record.scope_id,
                    "reason": record.reason,
                    "attempt_count": record.attempt_count,
                },
            )
        except Exception as exc:
            await self._handle_failure(record, exc)

    async def _handle_failure(
        self,
        record: CacheInvalidationOutboxRecord,
        exc: Exception,
    ) -> None:
        error = str(exc)
        if record.attempt_count >= min(record.max_attempts, self.config.max_attempts):
            failed = await self.repository.mark_failed(
                record.invalidation_id,
                worker_id=self.worker_id,
                error=error,
            )
            if not failed:
                self._log_transition_skipped(record, transition="failed", error=error)
                return
            logger.error(
                "cache invalidation outbox record failed permanently",
                extra={
                    "invalidation_id": record.invalidation_id,
                    "scope_type": record.scope_type,
                    "scope_id": record.scope_id,
                    "reason": record.reason,
                    "attempt_count": record.attempt_count,
                    "error": error[:200],
                },
            )
            return

        retry_delay_seconds = self._retry_delay_seconds(record.attempt_count)
        retry_scheduled = await self.repository.mark_retry(
            record.invalidation_id,
            worker_id=self.worker_id,
            error=error,
            next_attempt_at=datetime.now(tz=UTC) + timedelta(seconds=retry_delay_seconds),
        )
        if not retry_scheduled:
            self._log_transition_skipped(
                record,
                transition="retry",
                error=error,
                retry_delay_seconds=retry_delay_seconds,
            )
            return
        logger.warning(
            "cache invalidation outbox record retry scheduled",
            extra={
                "invalidation_id": record.invalidation_id,
                "scope_type": record.scope_type,
                "scope_id": record.scope_id,
                "reason": record.reason,
                "attempt_count": record.attempt_count,
                "retry_delay_seconds": retry_delay_seconds,
                "error": error[:200],
            },
        )

    async def _invalidate_record(self, record: CacheInvalidationOutboxRecord) -> None:
        self._require_cache_invalidation_backend(record.scope_type)
        if record.scope_type == "organization":
            await self.key_service.invalidate_keys_for_org(record.scope_id)
            return
        if record.scope_type == "team":
            await self.key_service.invalidate_keys_for_team(record.scope_id)
            return
        if record.scope_type == "user":
            await self.key_service.invalidate_keys_for_user(record.scope_id)
            return
        if record.scope_type == "key_hash":
            await self.key_service.invalidate_key_cache_by_hash(record.scope_id)
            return
        raise ValueError(f"Unsupported cache invalidation scope_type: {record.scope_type}")

    async def _invalidate_record_with_timeout(self, record: CacheInvalidationOutboxRecord) -> None:
        try:
            await asyncio.wait_for(
                self._invalidate_record(record),
                timeout=self._record_timeout_seconds(),
            )
        except TimeoutError as exc:
            raise TimeoutError("cache invalidation record timed out") from exc

    def _require_cache_invalidation_backend(self, scope_type: str) -> None:
        if self.key_service is None:
            raise CacheInvalidationBackendUnavailable("key service unavailable")
        require_backend = getattr(self.key_service, "require_cache_invalidation_backend", None)
        if require_backend is not None:
            require_backend(scope_type=scope_type)

    def _retry_delay_seconds(self, attempt_count: int) -> int:
        initial = max(1, int(self.config.retry_initial_seconds))
        max_delay = max(initial, int(self.config.retry_max_seconds))
        return min(initial * max(1, 2 ** max(0, attempt_count - 1)), max_delay)

    def _record_timeout_seconds(self) -> float:
        configured_timeout = max(0.001, float(self.config.record_timeout_seconds))
        lease_window = max(0.001, float(max(1, int(self.config.lease_seconds))) - 0.5)
        return min(configured_timeout, lease_window)

    def _log_transition_skipped(
        self,
        record: CacheInvalidationOutboxRecord,
        *,
        transition: str,
        error: str | None = None,
        retry_delay_seconds: int | None = None,
    ) -> None:
        extra: dict[str, Any] = {
            "invalidation_id": record.invalidation_id,
            "scope_type": record.scope_type,
            "scope_id": record.scope_id,
            "reason": record.reason,
            "attempt_count": record.attempt_count,
            "worker_id": self.worker_id,
            "transition": transition,
        }
        if error is not None:
            extra["error"] = error[:200]
        if retry_delay_seconds is not None:
            extra["retry_delay_seconds"] = retry_delay_seconds
        logger.warning("cache invalidation outbox transition skipped", extra=extra)


__all__ = [
    "CacheInvalidationWorker",
    "CacheInvalidationWorkerConfig",
]
