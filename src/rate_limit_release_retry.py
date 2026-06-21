from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
import logging
import random
from time import perf_counter
from typing import Any, Callable

from src.rate_limit_policy import RateLimitLease, release_rate_limit_controls
from src.services.limit_counter import LimitCounter

logger = logging.getLogger(__name__)

_DEFAULT_RELEASE_RETRY_DRAIN_LIMIT = 16
_DEFAULT_RELEASE_RETRY_QUEUE_LIMIT = 1024
_DEFAULT_RELEASE_RETRY_INITIAL_SECONDS = 0.5
_DEFAULT_RELEASE_RETRY_MAX_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class _RateLimitReleaseRetry:
    limiter: LimitCounter
    lease: RateLimitLease
    attempt_count: int
    next_attempt_at: float


def rate_limit_release_retry_delay_seconds(attempt_count: int) -> float:
    exponential_delay = _DEFAULT_RELEASE_RETRY_INITIAL_SECONDS * (2 ** max(0, int(attempt_count)))
    capped_delay = min(_DEFAULT_RELEASE_RETRY_MAX_SECONDS, exponential_delay)
    jitter = random.uniform(0.0, capped_delay * 0.2)
    return capped_delay + jitter


class RateLimitReleaseRetryQueue:
    def __init__(
        self,
        *,
        max_size: int = _DEFAULT_RELEASE_RETRY_QUEUE_LIMIT,
        drain_limit: int = _DEFAULT_RELEASE_RETRY_DRAIN_LIMIT,
        delay_seconds: Callable[[int], float] = rate_limit_release_retry_delay_seconds,
        auto_start: bool = True,
    ) -> None:
        self._queue: deque[_RateLimitReleaseRetry] = deque()
        self._max_size = max(1, int(max_size))
        self._drain_limit = max(1, int(drain_limit))
        self._delay_seconds = delay_seconds
        self._auto_start = auto_start
        self._task: asyncio.Task[None] | None = None
        self._wakeup: asyncio.Event | None = None
        self._stopped = False

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    def enqueue(
        self,
        *,
        limiter: LimitCounter,
        lease: RateLimitLease,
        attempt_count: int = 0,
    ) -> bool:
        if not lease.pending_parallel_acquisitions:
            return True

        delay_seconds = max(0.0, float(self._delay_seconds(attempt_count)))
        inserted = self._insert_retry(
            _RateLimitReleaseRetry(
                limiter=limiter,
                lease=lease,
                attempt_count=max(0, int(attempt_count)),
                next_attempt_at=perf_counter() + delay_seconds,
            )
        )
        if inserted:
            if self._auto_start:
                self._ensure_worker()
                self._wake_worker()
        return inserted

    async def drain_due(self, *, max_releases: int | None = None) -> None:
        attempts = 0
        now = perf_counter()
        release_limit = self._drain_limit if max_releases is None else max(0, int(max_releases))
        while self._queue and attempts < release_limit and self._queue[0].next_attempt_at <= now:
            retry = self._queue.popleft()
            attempts += 1
            released = await self._release(retry)
            if not released:
                self.enqueue(
                    limiter=retry.limiter,
                    lease=retry.lease,
                    attempt_count=retry.attempt_count + 1,
                )

    async def stop(self) -> None:
        self._stopped = True
        self._wake_worker()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    def _insert_retry(self, retry: _RateLimitReleaseRetry) -> bool:
        if len(self._queue) >= self._max_size:
            logger.error(
                "rate-limit release retry queue full pending=%s",
                len(self._queue),
            )
            return False
        for index, queued in enumerate(self._queue):
            if retry.next_attempt_at < queued.next_attempt_at:
                self._queue.insert(index, retry)
                return True
        self._queue.append(retry)
        return True

    async def _release(self, retry: _RateLimitReleaseRetry) -> bool:
        if not retry.lease.pending_parallel_acquisitions:
            return True
        try:
            await release_rate_limit_controls(limiter=retry.limiter, lease=retry.lease)
        except Exception as exc:
            pending_count = len(retry.lease.pending_parallel_acquisitions)
            logger.warning(
                "rate-limit release retry failed pending=%s attempt=%s error=%s",
                pending_count,
                retry.attempt_count + 1,
                exc,
                exc_info=True,
            )
            return pending_count == 0
        return not bool(retry.lease.pending_parallel_acquisitions)

    def _ensure_worker(self) -> None:
        if self._stopped:
            return
        if self._task is not None and not self._task.done():
            return
        self._wakeup = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    def _wake_worker(self) -> None:
        if self._wakeup is not None:
            self._wakeup.set()

    async def _run(self) -> None:
        while not self._stopped:
            if not self._queue:
                return
            delay_seconds = max(0.0, self._queue[0].next_attempt_at - perf_counter())
            if delay_seconds > 0:
                wakeup = self._wakeup
                if wakeup is None:
                    self._wakeup = asyncio.Event()
                    wakeup = self._wakeup
                try:
                    await asyncio.wait_for(wakeup.wait(), timeout=delay_seconds)
                except TimeoutError:
                    pass
                wakeup.clear()
                continue
            await self.drain_due()


def get_rate_limit_release_retry_queue(app: Any) -> RateLimitReleaseRetryQueue:
    state = getattr(app, "state", app)
    queue = getattr(state, "rate_limit_release_retry_queue", None)
    if isinstance(queue, RateLimitReleaseRetryQueue):
        return queue
    queue = RateLimitReleaseRetryQueue()
    setattr(state, "rate_limit_release_retry_queue", queue)
    return queue
