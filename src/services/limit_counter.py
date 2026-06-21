from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import math
import secrets
import time
from typing import Any, Literal

from src.models.errors import RateLimitError, ServiceUnavailableError

_PARALLEL_LEASE_TTL_SECONDS = 300


@dataclass(frozen=True)
class RateLimitCheck:
    scope: str
    entity_id: str
    limit: int
    amount: int = 1
    window_seconds: int = 60


@dataclass(frozen=True)
class ParallelLimitCheck:
    scope: str
    entity_id: str
    limit: int


@dataclass(frozen=True)
class LegacyParallelLease:
    scope: str
    entity_id: str
    limit: int
    backend: Literal["redis", "fallback"]
    ttl_seconds: int = _PARALLEL_LEASE_TTL_SECONDS

    @property
    def check(self) -> ParallelLimitCheck:
        return ParallelLimitCheck(scope=self.scope, entity_id=self.entity_id, limit=self.limit)


@dataclass(frozen=True)
class ParallelLimitLease:
    scope: str
    entity_id: str
    limit: int
    token: str
    backend: Literal["redis", "fallback"]
    ttl_seconds: int = _PARALLEL_LEASE_TTL_SECONDS

    @property
    def check(self) -> ParallelLimitCheck:
        return ParallelLimitCheck(scope=self.scope, entity_id=self.entity_id, limit=self.limit)


@dataclass(frozen=True)
class _ParallelLeaseGroup:
    check: ParallelLimitCheck
    requested_count: int


@dataclass
class RateLimitResult:
    checks: list[RateLimitCheck] = field(default_factory=list)
    current_values: list[int] = field(default_factory=list)
    window_reset_at: int = 0
    window_resets: list[int] = field(default_factory=list)


class LimitCounter:
    def __init__(self, redis_client: Any | None = None, degraded_mode: str = "fail_open") -> None:
        self.redis = redis_client
        self.degraded_mode = degraded_mode if degraded_mode in {"fail_open", "fail_closed"} else "fail_open"
        self._fallback_counters: dict[str, tuple[int, int]] = {}
        self._fallback_parallel: dict[str, int] = {}
        self._fallback_lock = asyncio.Lock()

    @staticmethod
    def _window_id(window_seconds: int) -> int:
        return math.floor(time.time() / window_seconds)

    async def check_rate_limit(self, scope: str, entity_id: str, limit: int | None, amount: int = 1) -> None:
        if limit is None or limit <= 0:
            return
        if self.redis is None:
            await self._check_rate_limit_fallback(scope, entity_id, limit, amount)
            return

        window_seconds = 60
        window_id = self._window_id(window_seconds)
        key = f"ratelimit:{scope}:{entity_id}:{window_id}"

        try:
            current = await self.redis.incrby(key, amount)
        except Exception:
            await self._handle_redis_degraded()
            await self._check_rate_limit_fallback(scope, entity_id, limit, amount)
            return
        if current == amount:
            try:
                await self.redis.expire(key, window_seconds)
            except Exception:
                await self._handle_redis_degraded()

        if current > limit:
            retry_after = window_seconds - int(time.time() % window_seconds)
            raise RateLimitError(retry_after=retry_after)

    async def check_rate_limits_atomic(self, checks: list[RateLimitCheck]) -> RateLimitResult:
        """Atomically validate and increment rate limits for multiple scopes.

        Each check carries its own ``window_seconds`` so minute, hour and day
        windows can be enforced in a single atomic call.

        Returns a RateLimitResult with post-increment counter values for each check.
        """
        normalized = [check for check in checks if check.limit > 0 and check.amount > 0]
        if not normalized:
            return RateLimitResult()

        now = time.time()
        per_check_resets = [
            int((math.floor(now / c.window_seconds) + 1) * c.window_seconds) for c in normalized
        ]
        min_window = min(c.window_seconds for c in normalized)
        window_reset_at = int((math.floor(now / min_window) + 1) * min_window)

        if self.redis is None:
            return await self._check_rate_limits_fallback(normalized, window_reset_at, per_check_resets)

        keys = [
            f"ratelimit:{check.scope}:{check.entity_id}:{self._window_id(check.window_seconds)}"
            for check in normalized
        ]
        amounts = [str(int(check.amount)) for check in normalized]
        limits = [str(int(check.limit)) for check in normalized]
        ttls = [str(int(check.window_seconds)) for check in normalized]

        script = """
local n = #KEYS
for i = 1, n do
  local current = tonumber(redis.call('GET', KEYS[i]) or '0')
  local amount = tonumber(ARGV[i]) or 0
  local limit = tonumber(ARGV[n + i]) or 0
  if current + amount > limit then
    return {0, i}
  end
end
local results = {1, 0}
for i = 1, n do
  local amount = tonumber(ARGV[i]) or 0
  local ttl = tonumber(ARGV[(2 * n) + i]) or 60
  local new_val = redis.call('INCRBY', KEYS[i], amount)
  redis.call('EXPIRE', KEYS[i], ttl)
  results[i + 2] = new_val
end
return results
"""
        try:
            raw = await self.redis.eval(script, len(keys), *keys, *amounts, *limits, *ttls)
        except Exception:
            await self._handle_redis_degraded()
            return await self._check_rate_limits_fallback(normalized, window_reset_at, per_check_resets)
        ok = int(raw[0]) if isinstance(raw, (list, tuple)) and len(raw) >= 1 else 1
        if ok == 1:
            current_values = []
            if isinstance(raw, (list, tuple)) and len(raw) > 2:
                current_values = [int(raw[i + 2]) for i in range(len(normalized)) if i + 2 < len(raw)]
            return RateLimitResult(
                checks=normalized,
                current_values=current_values,
                window_reset_at=window_reset_at,
                window_resets=per_check_resets,
            )

        failed_index = int(raw[1]) - 1 if isinstance(raw, (list, tuple)) and len(raw) >= 2 else 0
        failed = normalized[max(0, failed_index)]
        retry_after = failed.window_seconds - int(now % failed.window_seconds)
        raise RateLimitError(
            message=f"Rate limit exceeded for scope '{failed.scope}'",
            param=failed.scope,
            code=f"{failed.scope}_exceeded",
            retry_after=retry_after,
        )

    async def acquire_parallel(self, scope: str, entity_id: str, limit: int | None) -> None:
        await self.acquire_legacy_parallel_lease(scope, entity_id, limit)

    async def release_parallel(self, scope: str, entity_id: str) -> None:
        if self.redis is None:
            await self._release_parallel_fallback(scope, entity_id)
            return
        key = _legacy_parallel_key(scope, entity_id)
        script = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0') or 0
if current <= 1 then
  redis.call('DEL', KEYS[1])
  return {1, 0}
end
local next_value = redis.call('DECR', KEYS[1])
return {1, next_value}
"""
        try:
            await self.redis.eval(script, 1, key)
        except Exception:
            await self._handle_redis_degraded()
            await self._release_parallel_fallback(scope, entity_id)

    async def acquire_legacy_parallel_lease(
        self,
        scope: str,
        entity_id: str,
        limit: int | None,
        *,
        ttl_seconds: int = _PARALLEL_LEASE_TTL_SECONDS,
    ) -> LegacyParallelLease | None:
        if limit is None or limit <= 0 or not entity_id:
            return None

        normalized_ttl_seconds = max(1, int(ttl_seconds))
        normalized_limit = int(limit)
        if self.redis is None:
            await self._acquire_parallel_fallback(scope, entity_id, normalized_limit)
            return LegacyParallelLease(
                scope=scope,
                entity_id=entity_id,
                limit=normalized_limit,
                backend="fallback",
                ttl_seconds=normalized_ttl_seconds,
            )

        key = _legacy_parallel_key(scope, entity_id)
        script = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0') or 0
if current < 0 then
  current = 0
end
local limit = tonumber(ARGV[1]) or 0
local ttl_seconds = tonumber(ARGV[2]) or 300
if current + 1 > limit then
  return {0, current}
end
local next_value = current + 1
redis.call('SET', KEYS[1], next_value)
redis.call('EXPIRE', KEYS[1], ttl_seconds)
return {1, next_value}
"""
        try:
            raw = await self.redis.eval(
                script,
                1,
                key,
                str(normalized_limit),
                str(normalized_ttl_seconds),
            )
        except Exception:
            await self._handle_redis_degraded()
            await self._acquire_parallel_fallback(scope, entity_id, normalized_limit)
            return LegacyParallelLease(
                scope=scope,
                entity_id=entity_id,
                limit=normalized_limit,
                backend="fallback",
                ttl_seconds=normalized_ttl_seconds,
            )

        ok = int(raw[0]) if isinstance(raw, (list, tuple)) and len(raw) >= 1 else 1
        if ok == 1:
            return LegacyParallelLease(
                scope=scope,
                entity_id=entity_id,
                limit=normalized_limit,
                backend="redis",
                ttl_seconds=normalized_ttl_seconds,
            )
        raise _parallel_limit_error(scope)

    async def release_legacy_parallel_lease(self, lease: LegacyParallelLease) -> None:
        if lease.backend == "fallback":
            await self._release_parallel_fallback(lease.scope, lease.entity_id)
            return
        if lease.backend != "redis":
            raise ServiceUnavailableError(message="Rate limit backend unavailable")
        if self.redis is None:
            raise ServiceUnavailableError(message="Rate limit backend unavailable")

        key = _legacy_parallel_key(lease.scope, lease.entity_id)
        script = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0') or 0
if current <= 1 then
  redis.call('DEL', KEYS[1])
  return {1, 0}
end
local next_value = redis.call('DECR', KEYS[1])
return {1, next_value}
"""
        try:
            await self.redis.eval(script, 1, key)
        except Exception as exc:
            raise ServiceUnavailableError(message="Rate limit backend unavailable") from exc

    async def refresh_legacy_parallel_lease(
        self,
        lease: LegacyParallelLease,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        if lease.backend != "redis":
            return
        if self.redis is None:
            raise ServiceUnavailableError(message="Rate limit backend unavailable")

        key = _legacy_parallel_key(lease.scope, lease.entity_id)
        normalized_ttl_seconds = max(1, int(ttl_seconds if ttl_seconds is not None else lease.ttl_seconds))
        script = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0') or 0
if current <= 0 then
  redis.call('DEL', KEYS[1])
  return {1, 0}
end
redis.call('EXPIRE', KEYS[1], ARGV[1])
return {1, current}
"""
        try:
            await self.redis.eval(script, 1, key, str(normalized_ttl_seconds))
        except Exception as exc:
            raise ServiceUnavailableError(message="Rate limit backend unavailable") from exc

    async def acquire_parallel_leases(
        self,
        checks: list[ParallelLimitCheck],
        *,
        ttl_seconds: int = _PARALLEL_LEASE_TTL_SECONDS,
    ) -> tuple[ParallelLimitLease, ...]:
        groups = _coalesce_parallel_limit_checks(checks)
        if not groups:
            return ()

        normalized_ttl_seconds = max(1, int(ttl_seconds))
        if self.redis is None:
            return await self._acquire_parallel_leases_fallback(
                groups,
                ttl_seconds=normalized_ttl_seconds,
            )

        leases = tuple(
            ParallelLimitLease(
                scope=group.check.scope,
                entity_id=group.check.entity_id,
                limit=group.check.limit,
                token=secrets.token_urlsafe(18),
                backend="redis",
                ttl_seconds=normalized_ttl_seconds,
            )
            for group in groups
            for _ in range(group.requested_count)
        )
        keys = [_parallel_lease_key(group.check.scope, group.check.entity_id) for group in groups]
        now_ms = int(time.time() * 1000)
        expires_at_ms = now_ms + (normalized_ttl_seconds * 1000)
        limits = [str(int(group.check.limit)) for group in groups]
        requested_counts = [str(int(group.requested_count)) for group in groups]
        tokens = [lease.token for lease in leases]

        script = """
local n = #KEYS
local now_ms = tonumber(ARGV[1]) or 0
local expires_at_ms = tonumber(ARGV[2]) or 0
for i = 1, n do
  redis.call('ZREMRANGEBYSCORE', KEYS[i], '-inf', now_ms)
  local limit = tonumber(ARGV[2 + i]) or 0
  local requested = tonumber(ARGV[2 + n + i]) or 0
  local current = redis.call('ZCARD', KEYS[i])
  if current + requested > limit then
    return {0, i}
  end
end
local token_index = 2 + (2 * n)
for i = 1, n do
  local requested = tonumber(ARGV[2 + n + i]) or 0
  for _ = 1, requested do
    token_index = token_index + 1
    local token = ARGV[token_index]
    redis.call('ZADD', KEYS[i], expires_at_ms, token)
  end
  redis.call('EXPIRE', KEYS[i], math.ceil((expires_at_ms - now_ms) / 1000))
end
return {1, 0}
"""
        try:
            raw = await self.redis.eval(
                script,
                len(keys),
                *keys,
                str(now_ms),
                str(expires_at_ms),
                *limits,
                *requested_counts,
                *tokens,
            )
        except Exception:
            await self._handle_redis_degraded()
            return await self._acquire_parallel_leases_fallback(
                groups,
                ttl_seconds=normalized_ttl_seconds,
            )

        ok = int(raw[0]) if isinstance(raw, (list, tuple)) and len(raw) >= 1 else 1
        if ok == 1:
            return leases

        failed_index = int(raw[1]) - 1 if isinstance(raw, (list, tuple)) and len(raw) >= 2 else 0
        failed = groups[max(0, min(failed_index, len(groups) - 1))].check
        raise _parallel_limit_error(failed.scope)

    async def release_parallel_leases(self, leases: list[ParallelLimitLease]) -> None:
        if not leases:
            return

        fallback_leases = [lease for lease in leases if lease.backend == "fallback"]
        redis_leases = [lease for lease in leases if lease.backend == "redis"]

        if redis_leases:
            if self.redis is None:
                raise ServiceUnavailableError(message="Rate limit backend unavailable")

            keys = [_parallel_lease_key(lease.scope, lease.entity_id) for lease in redis_leases]
            tokens = [lease.token for lease in redis_leases]
            script = """
local n = #KEYS
for i = 1, n do
  redis.call('ZREM', KEYS[i], ARGV[i])
  if redis.call('ZCARD', KEYS[i]) == 0 then
    redis.call('DEL', KEYS[i])
  end
end
return {1, 0}
"""
            try:
                await self.redis.eval(script, len(keys), *keys, *tokens)
            except Exception as exc:
                raise ServiceUnavailableError(message="Rate limit backend unavailable") from exc

        if fallback_leases:
            await self._release_parallel_leases_fallback(fallback_leases)

    async def refresh_parallel_leases(
        self,
        leases: list[ParallelLimitLease],
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        if not leases:
            return

        redis_leases = [lease for lease in leases if lease.backend == "redis"]
        if not redis_leases:
            return
        if self.redis is None:
            raise ServiceUnavailableError(message="Rate limit backend unavailable")

        keys = [_parallel_lease_key(lease.scope, lease.entity_id) for lease in redis_leases]
        tokens = [lease.token for lease in redis_leases]
        now_ms = int(time.time() * 1000)
        expires_at_values = [
            str(now_ms + (max(1, int(ttl_seconds if ttl_seconds is not None else lease.ttl_seconds)) * 1000))
            for lease in redis_leases
        ]
        script = """
local n = #KEYS
local now_ms = tonumber(ARGV[(2 * n) + 1]) or 0
for i = 1, n do
  local token = ARGV[i]
  local expires_at_ms = tonumber(ARGV[n + i]) or 0
  if redis.call('ZSCORE', KEYS[i], token) then
    redis.call('ZADD', KEYS[i], expires_at_ms, token)
    redis.call('EXPIRE', KEYS[i], math.ceil((expires_at_ms - now_ms) / 1000))
  end
end
return {1, 0}
"""
        try:
            await self.redis.eval(script, len(keys), *keys, *tokens, *expires_at_values, str(now_ms))
        except Exception as exc:
            raise ServiceUnavailableError(message="Rate limit backend unavailable") from exc

    async def _handle_redis_degraded(self) -> None:
        if self.degraded_mode == "fail_closed":
            raise ServiceUnavailableError(message="Rate limit backend unavailable")

    async def _check_rate_limits_fallback(
        self, checks: list[RateLimitCheck], window_reset_at: int = 0,
        per_check_resets: list[int] | None = None,
    ) -> RateLimitResult:
        if self.degraded_mode == "fail_closed":
            raise ServiceUnavailableError(message="Rate limit backend unavailable")

        normalized = [check for check in checks if check.limit > 0 and check.amount > 0]
        if not normalized:
            return RateLimitResult()

        now = int(time.time())
        if window_reset_at == 0:
            min_window = min(c.window_seconds for c in normalized)
            window_reset_at = int((math.floor(now / min_window) + 1) * min_window)
        if per_check_resets is None:
            per_check_resets = [
                int((math.floor(now / c.window_seconds) + 1) * c.window_seconds) for c in normalized
            ]
        pending_updates: list[tuple[str, int, int]] = []

        async with self._fallback_lock:
            for check in normalized:
                ws = check.window_seconds
                window_id = self._window_id(ws)
                key = f"{check.scope}:{check.entity_id}:{window_id}"
                expiry, current = self._fallback_counters.get(key, (now + ws, 0))
                if expiry <= now:
                    expiry, current = now + ws, 0
                next_value = current + check.amount
                if next_value > check.limit:
                    retry_after = max(1, expiry - now)
                    raise RateLimitError(
                        message=f"Rate limit exceeded for scope '{check.scope}'",
                        param=check.scope,
                        code=f"{check.scope}_exceeded",
                        retry_after=retry_after,
                    )
                pending_updates.append((key, expiry, next_value))

            current_values = []
            for key, expiry, next_value in pending_updates:
                self._fallback_counters[key] = (expiry, next_value)
                current_values.append(next_value)

        return RateLimitResult(
            checks=normalized,
            current_values=current_values,
            window_reset_at=window_reset_at,
            window_resets=per_check_resets,
        )

    async def _check_rate_limit_fallback(self, scope: str, entity_id: str, limit: int, amount: int) -> None:
        if self.degraded_mode == "fail_closed":
            raise ServiceUnavailableError(message="Rate limit backend unavailable")
        window_seconds = 60
        window_id = self._window_id(window_seconds)
        key = f"{scope}:{entity_id}:{window_id}"
        now = int(time.time())
        async with self._fallback_lock:
            expiry, current = self._fallback_counters.get(key, (now + window_seconds, 0))
            if expiry <= now:
                expiry, current = now + window_seconds, 0
            current += amount
            self._fallback_counters[key] = (expiry, current)
        if current > limit:
            retry_after = max(1, expiry - now)
            raise RateLimitError(
                message=f"Rate limit exceeded for scope '{scope}'",
                param=scope,
                code=f"{scope}_exceeded",
                retry_after=retry_after,
            )

    async def _acquire_parallel_fallback(self, scope: str, entity_id: str, limit: int) -> None:
        if self.degraded_mode == "fail_closed":
            raise ServiceUnavailableError(message="Rate limit backend unavailable")
        key = f"{scope}:{entity_id}"
        async with self._fallback_lock:
            current = int(self._fallback_parallel.get(key, 0)) + 1
            self._fallback_parallel[key] = current
        if current > limit:
            async with self._fallback_lock:
                self._fallback_parallel[key] = max(0, int(self._fallback_parallel.get(key, 1)) - 1)
            raise _parallel_limit_error(scope)

    async def _release_parallel_fallback(self, scope: str, entity_id: str) -> None:
        key = f"{scope}:{entity_id}"
        async with self._fallback_lock:
            current = max(0, int(self._fallback_parallel.get(key, 0)) - 1)
            if current == 0:
                self._fallback_parallel.pop(key, None)
            else:
                self._fallback_parallel[key] = current

    async def _acquire_parallel_leases_fallback(
        self,
        groups: tuple[_ParallelLeaseGroup, ...],
        *,
        ttl_seconds: int,
    ) -> tuple[ParallelLimitLease, ...]:
        if self.degraded_mode == "fail_closed":
            raise ServiceUnavailableError(message="Rate limit backend unavailable")

        async with self._fallback_lock:
            for group in groups:
                key = f"{group.check.scope}:{group.check.entity_id}"
                current = int(self._fallback_parallel.get(key, 0))
                if current + group.requested_count > group.check.limit:
                    raise _parallel_limit_error(group.check.scope)

            leases = tuple(
                ParallelLimitLease(
                    scope=group.check.scope,
                    entity_id=group.check.entity_id,
                    limit=group.check.limit,
                    token=secrets.token_urlsafe(18),
                    backend="fallback",
                    ttl_seconds=ttl_seconds,
                )
                for group in groups
                for _ in range(group.requested_count)
            )
            for lease in leases:
                key = f"{lease.scope}:{lease.entity_id}"
                self._fallback_parallel[key] = int(self._fallback_parallel.get(key, 0)) + 1
            return leases

    async def _release_parallel_leases_fallback(self, leases: list[ParallelLimitLease]) -> None:
        async with self._fallback_lock:
            for lease in leases:
                key = f"{lease.scope}:{lease.entity_id}"
                current = max(0, int(self._fallback_parallel.get(key, 0)) - 1)
                if current == 0:
                    self._fallback_parallel.pop(key, None)
                else:
                    self._fallback_parallel[key] = current


def _parallel_limit_error(scope: str) -> RateLimitError:
    if scope == "key":
        return RateLimitError(message="Parallel request limit exceeded", retry_after=1)
    return RateLimitError(
        message=f"Parallel request limit exceeded for scope '{scope}'",
        param=scope,
        code=_parallel_limit_error_code(scope),
        retry_after=1,
    )


def _parallel_limit_error_code(scope: str) -> str:
    return f"{scope}_exceeded" if scope.endswith("_parallel") else f"{scope}_parallel_exceeded"


def _parallel_lease_key(scope: str, entity_id: str) -> str:
    return f"parallel_lease:{scope}:{entity_id}"


def _legacy_parallel_key(scope: str, entity_id: str) -> str:
    return f"parallel:{scope}:{entity_id}"


def _coalesce_parallel_limit_checks(checks: list[ParallelLimitCheck]) -> tuple[_ParallelLeaseGroup, ...]:
    groups: dict[tuple[str, str], _ParallelLeaseGroup] = {}
    for check in checks:
        if check.limit <= 0 or not check.entity_id:
            continue
        key = (check.scope, check.entity_id)
        existing = groups.get(key)
        if existing is None:
            groups[key] = _ParallelLeaseGroup(
                check=ParallelLimitCheck(
                    scope=check.scope,
                    entity_id=check.entity_id,
                    limit=int(check.limit),
                ),
                requested_count=1,
            )
            continue
        groups[key] = _ParallelLeaseGroup(
            check=ParallelLimitCheck(
                scope=existing.check.scope,
                entity_id=existing.check.entity_id,
                limit=min(existing.check.limit, int(check.limit)),
            ),
            requested_count=existing.requested_count + 1,
        )
    return tuple(groups.values())
