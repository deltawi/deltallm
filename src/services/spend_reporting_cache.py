from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime
import hashlib
import json
import logging
import random
from time import monotonic
from typing import Any
from uuid import uuid4


logger = logging.getLogger(__name__)

_CACHE_NAMESPACE = "ui:spend-report:v2"
_SHORT_RANGE_DAYS = 45
_MEDIUM_RANGE_DAYS = 210
_SHORT_TTL_SECONDS = 30
_MEDIUM_TTL_SECONDS = 60
_LONG_TTL_SECONDS = 300
_LOCK_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""
_LOCK_RENEW_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
_LOCKED_CACHE_WRITE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('SETEX', KEYS[2], ARGV[2], ARGV[3])
  return 1
end
return 0
"""


def reporting_cache_ttl(start_date: date | None, end_date: date | None) -> int:
    if start_date is None:
        return _LONG_TTL_SECONDS

    effective_end = end_date or datetime.now(tz=UTC).date()
    inclusive_days = max(1, (effective_end - start_date).days + 1)
    if inclusive_days <= _SHORT_RANGE_DAYS:
        return _SHORT_TTL_SECONDS
    if inclusive_days <= _MEDIUM_RANGE_DAYS:
        return _MEDIUM_TTL_SECONDS
    return _LONG_TTL_SECONDS


class ReportingRefreshBusy(RuntimeError):
    """Raised when another process is still rebuilding the same reporting key."""


class ReportingQueryTimedOut(RuntimeError):
    """Raised when a reporting loader exceeds its execution deadline."""


class ReportingLoadLimiter:
    """A per-worker concurrency limiter whose target can change safely at runtime."""

    def __init__(self, limit: int) -> None:
        self._limit = max(1, int(limit))
        self._active = 0
        self._condition = asyncio.Condition()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def active(self) -> int:
        return self._active

    async def acquire(self, timeout_seconds: float) -> None:
        async def wait_for_capacity() -> None:
            async with self._condition:
                await self._condition.wait_for(lambda: self._active < self._limit)
                self._active += 1

        try:
            await asyncio.wait_for(
                wait_for_capacity(),
                timeout=max(0.05, float(timeout_seconds)),
            )
        except TimeoutError as exc:
            raise ReportingRefreshBusy("Reporting query capacity is currently full") from exc

    async def release(self) -> None:
        async with self._condition:
            if self._active <= 0:  # pragma: no cover - defensive invariant guard
                logger.error("spend reporting limiter released without an active loader")
                return
            self._active -= 1
            self._condition.notify_all()

    async def reconfigure(self, limit: int) -> None:
        next_limit = max(1, int(limit))
        async with self._condition:
            if next_limit == self._limit:
                return
            previous_limit = self._limit
            self._limit = next_limit
            self._condition.notify_all()
        logger.info(
            "spend reporting concurrency updated from %s to %s; active_loaders=%s",
            previous_limit,
            next_limit,
            self._active,
        )


@dataclass(frozen=True)
class SpendReportingCacheResult:
    value: dict[str, Any]
    status: str

    @property
    def cache_hit(self) -> bool:
        return self.status in {"hit", "hit_after_lock", "coalesced_local", "coalesced_distributed"}


@dataclass(frozen=True)
class _CacheEntry:
    entry_id: str | None
    value: dict[str, Any]


@dataclass(frozen=True)
class ReportingLoadBudget:
    execution_timeout_seconds: float
    global_max_concurrent_loads: int


class SpendReportingCache:
    def __init__(
        self,
        redis_client: Any | None,
        *,
        lock_ttl_seconds: int = 30,
        wait_timeout_seconds: float = 10.0,
        poll_interval_seconds: float = 0.05,
        lock_renewal_interval_seconds: float | None = None,
        max_concurrent_loads: int = 2,
        global_max_concurrent_loads: int = 2,
        load_queue_timeout_seconds: float = 10.0,
        load_execution_timeout_seconds: float = 60.0,
        redis_operation_timeout_seconds: float = 0.5,
        load_limiter: ReportingLoadLimiter | None = None,
    ) -> None:
        self.redis = redis_client
        self.lock_ttl_seconds = max(5, int(lock_ttl_seconds))
        self.wait_timeout_seconds = max(0.05, float(wait_timeout_seconds))
        self.poll_interval_seconds = max(0.01, float(poll_interval_seconds))
        requested_renewal_interval = (
            self.lock_ttl_seconds / 3
            if lock_renewal_interval_seconds is None
            else float(lock_renewal_interval_seconds)
        )
        self.lock_renewal_interval_seconds = max(
            0.01,
            min(requested_renewal_interval, self.lock_ttl_seconds * 0.8),
        )
        self.load_queue_timeout_seconds = max(0.05, float(load_queue_timeout_seconds))
        self.load_execution_timeout_seconds = max(0.05, float(load_execution_timeout_seconds))
        self.global_max_concurrent_loads = max(1, int(global_max_concurrent_loads))
        self.redis_operation_timeout_seconds = max(0.01, float(redis_operation_timeout_seconds))
        self._load_limiter = load_limiter or ReportingLoadLimiter(max_concurrent_loads)
        self._active_load_budget: ContextVar[ReportingLoadBudget | None] = ContextVar(
            f"spend_reporting_load_budget_{id(self)}",
            default=None,
        )
        self._inflight: dict[str, asyncio.Task[SpendReportingCacheResult]] = {}
        self._inflight_guard = asyncio.Lock()
        self._last_warning_at: dict[str, float] = {}

    @property
    def load_limiter(self) -> ReportingLoadLimiter:
        return self._load_limiter

    @property
    def active_load_budget(self) -> ReportingLoadBudget:
        budget = self._active_load_budget.get()
        if budget is not None:
            return budget
        return ReportingLoadBudget(
            execution_timeout_seconds=self.load_execution_timeout_seconds,
            global_max_concurrent_loads=self.global_max_concurrent_loads,
        )

    async def reconfigure(
        self,
        *,
        max_concurrent_loads: int,
        global_max_concurrent_loads: int,
        load_queue_timeout_seconds: float,
        load_execution_timeout_seconds: float,
        redis_operation_timeout_seconds: float,
    ) -> None:
        self.load_queue_timeout_seconds = max(0.05, float(load_queue_timeout_seconds))
        self.load_execution_timeout_seconds = max(0.05, float(load_execution_timeout_seconds))
        self.global_max_concurrent_loads = max(1, int(global_max_concurrent_loads))
        self.redis_operation_timeout_seconds = max(0.01, float(redis_operation_timeout_seconds))
        await self._load_limiter.reconfigure(max_concurrent_loads)

    @staticmethod
    def key(payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"{_CACHE_NAMESPACE}:{digest}"

    def _warn_cache_failure(self, operation: str, exc: Exception) -> None:
        now = monotonic()
        if now - self._last_warning_at.get(operation, 0.0) < 60.0:
            return
        self._last_warning_at[operation] = now
        logger.warning("spend reporting cache %s failed: %s", operation, exc)

    @staticmethod
    def _decode_entry(raw: Any) -> _CacheEntry | None:
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        decoded = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(decoded, dict):
            return None

        value = decoded.get("value")
        if isinstance(value, dict) and decoded.get("version") == 2:
            entry_id = str(decoded.get("entry_id") or "").strip() or None
            return _CacheEntry(entry_id=entry_id, value=value)

        # Defensive compatibility for values written by a partially rolled-out worker.
        return _CacheEntry(entry_id=None, value=decoded)

    async def _read_entry(self, key: str) -> _CacheEntry | None:
        if self.redis is None:
            return None
        try:
            async with asyncio.timeout(self.redis_operation_timeout_seconds):
                return self._decode_entry(await self.redis.get(key))
        except Exception as exc:  # pragma: no cover - fail-open defensive guard
            self._warn_cache_failure("read", exc)
            return None

    async def get(self, key: str) -> dict[str, Any] | None:
        entry = await self._read_entry(key)
        return entry.value if entry is not None else None

    @staticmethod
    def _entry_envelope(value: dict[str, Any]) -> tuple[_CacheEntry, str]:
        entry = _CacheEntry(entry_id=uuid4().hex, value=value)
        envelope = {
            "version": 2,
            "entry_id": entry.entry_id,
            "stored_at": datetime.now(tz=UTC).isoformat(),
            "value": value,
        }
        payload = json.dumps(envelope, sort_keys=True, separators=(",", ":"), default=str)
        return entry, payload

    async def _write_entry(self, key: str, value: dict[str, Any], ttl_seconds: int) -> _CacheEntry:
        entry, payload = self._entry_envelope(value)
        if self.redis is None:
            return entry
        try:
            async with asyncio.timeout(self.redis_operation_timeout_seconds):
                await self.redis.setex(key, max(1, int(ttl_seconds)), payload)
        except Exception as exc:  # pragma: no cover - fail-open defensive guard
            self._warn_cache_failure("write", exc)
        return entry

    async def _write_entry_if_lock_owner(
        self,
        *,
        key: str,
        lock_key: str,
        token: str,
        value: dict[str, Any],
        ttl_seconds: int,
    ) -> bool | None:
        if self.redis is None:
            return None
        _, payload = self._entry_envelope(value)
        ttl = max(1, int(ttl_seconds))
        try:
            async with asyncio.timeout(self.redis_operation_timeout_seconds):
                if hasattr(self.redis, "eval"):
                    written = await self.redis.eval(
                        _LOCKED_CACHE_WRITE_SCRIPT,
                        2,
                        lock_key,
                        key,
                        token,
                        ttl,
                        payload,
                    )
                    return bool(written)

                # Test and compatibility fallback. Production Redis clients support EVAL.
                current = await self.redis.get(lock_key)
                if isinstance(current, bytes):
                    current = current.decode("utf-8")
                if current != token:
                    return False
                await self.redis.setex(key, ttl, payload)
                return True
        except Exception as exc:  # pragma: no cover - fail-open defensive guard
            self._warn_cache_failure("owned write", exc)
            return None

    async def set(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        await self._write_entry(key, value, ttl_seconds)

    async def _try_acquire_lock(self, lock_key: str, token: str) -> bool | None:
        if self.redis is None:
            return None
        try:
            async with asyncio.timeout(self.redis_operation_timeout_seconds):
                acquired = await self.redis.set(
                    lock_key,
                    token,
                    ex=self.lock_ttl_seconds,
                    nx=True,
                )
                return bool(acquired)
        except Exception as exc:  # pragma: no cover - fail-open defensive guard
            self._warn_cache_failure("lock acquisition", exc)
            return None

    async def _release_lock(self, lock_key: str, token: str) -> None:
        if self.redis is None:
            return
        try:
            async with asyncio.timeout(self.redis_operation_timeout_seconds):
                if hasattr(self.redis, "eval"):
                    await self.redis.eval(_LOCK_RELEASE_SCRIPT, 1, lock_key, token)
                    return

                # Test and compatibility fallback. Production Redis clients support EVAL.
                current = await self.redis.get(lock_key)
                if isinstance(current, bytes):
                    current = current.decode("utf-8")
                if current == token and hasattr(self.redis, "delete"):
                    await self.redis.delete(lock_key)
        except Exception as exc:  # pragma: no cover - lock TTL remains the final guard
            self._warn_cache_failure("lock release", exc)

    async def _renew_lock(self, lock_key: str, token: str) -> bool | None:
        if self.redis is None:
            return None
        try:
            async with asyncio.timeout(self.redis_operation_timeout_seconds):
                if hasattr(self.redis, "eval"):
                    renewed = await self.redis.eval(
                        _LOCK_RENEW_SCRIPT,
                        1,
                        lock_key,
                        token,
                        self.lock_ttl_seconds,
                    )
                    return bool(renewed)

                # Test and compatibility fallback. Production Redis clients support EVAL.
                current = await self.redis.get(lock_key)
                if isinstance(current, bytes):
                    current = current.decode("utf-8")
                if current != token or not hasattr(self.redis, "expire"):
                    return False
                return bool(await self.redis.expire(lock_key, self.lock_ttl_seconds))
        except Exception as exc:  # pragma: no cover - fail-open defensive guard
            self._warn_cache_failure("lock renewal", exc)
            return None

    async def _maintain_lock(
        self,
        lock_key: str,
        token: str,
        stop: asyncio.Event,
        lease_lost: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self.lock_renewal_interval_seconds,
                )
                return
            except TimeoutError:
                renewed = await self._renew_lock(lock_key, token)
                if renewed is not True:
                    lease_lost.set()
                    return

    async def _run_loader(
        self,
        loader: Callable[[], Awaitable[dict[str, Any]]],
        lease_lost: asyncio.Event,
    ) -> dict[str, Any]:
        await self._load_limiter.acquire(self.load_queue_timeout_seconds)

        try:
            if lease_lost.is_set():
                raise ReportingRefreshBusy("Reporting lock ownership was lost while waiting for capacity")
            budget = ReportingLoadBudget(
                execution_timeout_seconds=self.load_execution_timeout_seconds,
                global_max_concurrent_loads=self.global_max_concurrent_loads,
            )
            execution_timeout = budget.execution_timeout_seconds
            budget_token = self._active_load_budget.set(budget)
            try:
                async with asyncio.timeout(execution_timeout):
                    return await loader()
            except TimeoutError as exc:
                logger.warning(
                    "spend reporting loader timed out after %.3f seconds",
                    execution_timeout,
                )
                raise ReportingQueryTimedOut(
                    f"Reporting query exceeded its {execution_timeout:g} second execution deadline"
                ) from exc
            finally:
                self._active_load_budget.reset(budget_token)
        finally:
            await self._load_limiter.release()

    async def _run_fail_open_loader(
        self,
        loader: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        # Redis availability must not determine whether reporting queries can
        # saturate the database. Keep the per-worker loader guard in fail-open
        # mode while skipping only the distributed coordination and cache write.
        return await self.run_uncached(loader)

    async def run_uncached(
        self,
        loader: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Run uncached reporting work behind the shared capacity and deadline guards."""

        return await self._run_loader(loader, asyncio.Event())

    async def _wait_for_new_entry(
        self,
        key: str,
        *,
        baseline_entry_id: str | None,
        force_refresh: bool,
    ) -> _CacheEntry | None:
        deadline = monotonic() + self.wait_timeout_seconds
        interval = self.poll_interval_seconds
        while monotonic() < deadline:
            await asyncio.sleep(interval + random.uniform(0, interval * 0.2))
            entry = await self._read_entry(key)
            if entry is not None and (
                not force_refresh
                or (entry.entry_id is not None and entry.entry_id != baseline_entry_id)
            ):
                return entry
            interval = min(interval * 1.5, 0.25)
        return None

    async def _load_as_lock_owner(
        self,
        *,
        key: str,
        lock_key: str,
        token: str,
        ttl_seconds: int,
        loader: Callable[[], Awaitable[dict[str, Any]]],
        force_refresh: bool,
    ) -> SpendReportingCacheResult:
        stop_renewal = asyncio.Event()
        lease_lost = asyncio.Event()
        renewal_task: asyncio.Task[None] | None = None
        try:
            if not force_refresh:
                cached = await self._read_entry(key)
                if cached is not None:
                    return SpendReportingCacheResult(cached.value, "hit_after_lock")

            renewal_task = asyncio.create_task(
                self._maintain_lock(lock_key, token, stop_renewal, lease_lost)
            )
            value = await self._run_loader(loader, lease_lost)
            if lease_lost.is_set():
                return SpendReportingCacheResult(value, "lease_lost_uncached")

            written = await self._write_entry_if_lock_owner(
                key=key,
                lock_key=lock_key,
                token=token,
                value=value,
                ttl_seconds=ttl_seconds,
            )
            if written is not True:
                return SpendReportingCacheResult(value, "lease_lost_uncached")
            return SpendReportingCacheResult(value, "forced_refresh" if force_refresh else "miss")
        finally:
            stop_renewal.set()
            if renewal_task is not None:
                await renewal_task
            await self._release_lock(lock_key, token)

    async def _load_coordinated(
        self,
        *,
        key: str,
        ttl_seconds: int,
        loader: Callable[[], Awaitable[dict[str, Any]]],
        force_refresh: bool,
        baseline_entry_id: str | None,
    ) -> SpendReportingCacheResult:
        if self.redis is None:
            value = await self._run_fail_open_loader(loader)
            return SpendReportingCacheResult(value, "fail_open")

        lock_key = f"{key}:lock"
        token = uuid4().hex
        acquired = await self._try_acquire_lock(lock_key, token)
        if acquired is None:
            value = await self._run_fail_open_loader(loader)
            return SpendReportingCacheResult(value, "fail_open")
        if acquired:
            return await self._load_as_lock_owner(
                key=key,
                lock_key=lock_key,
                token=token,
                ttl_seconds=ttl_seconds,
                loader=loader,
                force_refresh=force_refresh,
            )

        entry = await self._wait_for_new_entry(
            key,
            baseline_entry_id=baseline_entry_id,
            force_refresh=force_refresh,
        )
        if entry is not None:
            return SpendReportingCacheResult(entry.value, "coalesced_distributed")

        retry_token = uuid4().hex
        retry_acquired = await self._try_acquire_lock(lock_key, retry_token)
        if retry_acquired is None:
            value = await self._run_fail_open_loader(loader)
            return SpendReportingCacheResult(value, "fail_open")
        if retry_acquired:
            return await self._load_as_lock_owner(
                key=key,
                lock_key=lock_key,
                token=retry_token,
                ttl_seconds=ttl_seconds,
                loader=loader,
                force_refresh=force_refresh,
            )

        raise ReportingRefreshBusy("An identical reporting query is still being refreshed")

    async def _remove_inflight(self, key: str, task: asyncio.Task[SpendReportingCacheResult]) -> None:
        async with self._inflight_guard:
            if self._inflight.get(key) is task:
                self._inflight.pop(key, None)

    def _schedule_inflight_cleanup(self, key: str, task: asyncio.Task[SpendReportingCacheResult]) -> None:
        try:
            asyncio.get_running_loop().create_task(self._remove_inflight(key, task))
        except RuntimeError:  # pragma: no cover - event loop shutdown
            return

    async def get_or_load(
        self,
        key: str,
        ttl_seconds: int,
        loader: Callable[[], Awaitable[dict[str, Any]]],
        *,
        force_refresh: bool = False,
    ) -> SpendReportingCacheResult:
        observed_entry = await self._read_entry(key)
        if observed_entry is not None and not force_refresh:
            return SpendReportingCacheResult(observed_entry.value, "hit")

        async with self._inflight_guard:
            task = self._inflight.get(key)
            coalesced_local = task is not None
            if task is None:
                task = asyncio.create_task(
                    self._load_coordinated(
                        key=key,
                        ttl_seconds=ttl_seconds,
                        loader=loader,
                        force_refresh=force_refresh,
                        baseline_entry_id=observed_entry.entry_id if observed_entry is not None else None,
                    )
                )
                self._inflight[key] = task
                task.add_done_callback(lambda done, cache_key=key: self._schedule_inflight_cleanup(cache_key, done))

        result = await asyncio.shield(task)
        if coalesced_local:
            return SpendReportingCacheResult(result.value, "coalesced_local")
        return result
