from __future__ import annotations

import asyncio
from contextlib import suppress
import logging

from src.rate_limit_policy import RateLimitLease
from src.services.limit_counter import LimitCounter

logger = logging.getLogger(__name__)
_DEFAULT_MAX_REFRESH_INTERVAL_SECONDS = 60.0


class RateLimitLeaseRefresher:
    def __init__(
        self,
        *,
        limiter: LimitCounter,
        lease: RateLimitLease,
        max_interval_seconds: float = _DEFAULT_MAX_REFRESH_INTERVAL_SECONDS,
    ) -> None:
        self._limiter = limiter
        self._lease = lease
        self._max_interval_seconds = max(1.0, float(max_interval_seconds))
        self._task: asyncio.Task[None] | None = None
        self._failure_count = 0

    def start(self) -> bool:
        if self._task is not None:
            return True
        if not self._has_refreshable_leases():
            return False
        self._task = asyncio.create_task(self._run())
        return True

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            interval_seconds = self._next_interval_seconds()
            await asyncio.sleep(interval_seconds)
            parallel_leases = list(self._lease.refreshable_parallel_leases)
            legacy_lease = self._lease.refreshable_legacy_parallel_lease
            if not parallel_leases and legacy_lease is None:
                return

            refresh_failed = False
            if parallel_leases:
                try:
                    await self._limiter.refresh_parallel_leases(parallel_leases)
                except Exception as exc:
                    refresh_failed = True
                    self._record_refresh_failure(exc)

            if legacy_lease is not None:
                try:
                    await self._limiter.refresh_legacy_parallel_lease(legacy_lease)
                except Exception as exc:
                    refresh_failed = True
                    self._record_refresh_failure(exc)

            if not refresh_failed:
                self._failure_count = 0

    def _record_refresh_failure(self, exc: Exception) -> None:
        self._failure_count += 1
        if self._failure_count == 1 or self._failure_count % 5 == 0:
            logger.warning(
                "rate-limit parallel lease refresh failed failures=%s error=%s",
                self._failure_count,
                exc,
                exc_info=True,
            )

    def _has_refreshable_leases(self) -> bool:
        return bool(self._lease.refreshable_parallel_leases or self._lease.refreshable_legacy_parallel_lease)

    def _refreshable_ttls(self) -> list[int]:
        ttls = [max(1, int(lease.ttl_seconds)) for lease in self._lease.refreshable_parallel_leases]
        legacy_lease = self._lease.refreshable_legacy_parallel_lease
        if legacy_lease is not None:
            ttls.append(max(1, int(legacy_lease.ttl_seconds)))
        return ttls

    def _next_interval_seconds(self) -> float:
        ttls = self._refreshable_ttls()
        if not ttls:
            return self._max_interval_seconds
        min_ttl = min(ttls)
        return max(1.0, min(self._max_interval_seconds, min_ttl / 3))
