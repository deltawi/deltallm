from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import math
import secrets
import time
from typing import Any, Literal

from src.models.errors import RateLimitError, ServiceUnavailableError

_PARALLEL_LEASE_TTL_SECONDS = 300
_FAIR_SHARE_ACTIVE_TTL_SECONDS = 120


@dataclass(frozen=True)
class FairShareLimit:
    organization_id: str
    weight: int
    tier_key: str | None = None
    strategy: Literal["weighted_fair", "reserved_burst"] = "weighted_fair"
    saturation_threshold: float = 0.8
    burst_multiplier: float = 1.0
    active_ttl_seconds: int = _FAIR_SHARE_ACTIVE_TTL_SECONDS


@dataclass(frozen=True)
class FairShareObservation:
    scope: str
    entity_id: str
    organization_id: str
    tier_key: str | None
    active_organizations: int
    effective_weight: float
    total_active_weight: float
    share_limit: int
    pool_limit: int
    pool_current: int
    saturated: bool
    capacity_boost_multiplier: float = 1.0


@dataclass(frozen=True)
class RateLimitCheck:
    scope: str
    entity_id: str
    limit: int
    amount: int = 1
    window_seconds: int = 60
    fair_share: FairShareLimit | None = None


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
    fair_share_observations: list[FairShareObservation] = field(default_factory=list)


class LimitCounter:
    def __init__(self, redis_client: Any | None = None, degraded_mode: str = "fail_open") -> None:
        self.redis = redis_client
        self.degraded_mode = degraded_mode if degraded_mode in {"fail_open", "fail_closed"} else "fail_open"
        self._fallback_counters: dict[str, tuple[int, int]] = {}
        self._fallback_parallel: dict[str, int] = {}
        self._fallback_fair_active: dict[str, dict[str, tuple[int, float]]] = {}
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

        if any(check.fair_share is not None for check in normalized):
            return await self._check_rate_limits_redis_fair_share(
                normalized,
                window_reset_at=window_reset_at,
                per_check_resets=per_check_resets,
                now=now,
            )

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

    async def _check_rate_limits_redis_fair_share(
        self,
        checks: list[RateLimitCheck],
        *,
        window_reset_at: int,
        per_check_resets: list[int],
        now: float,
    ) -> RateLimitResult:
        n = len(checks)
        window_ids = [self._window_id(check.window_seconds) for check in checks]
        global_keys = [
            f"ratelimit:{check.scope}:{check.entity_id}:{window_ids[index]}"
            for index, check in enumerate(checks)
        ]
        org_keys = [
            _fair_share_org_usage_key(check, window_ids[index])
            if check.fair_share is not None
            else global_keys[index]
            for index, check in enumerate(checks)
        ]
        active_keys = [
            _fair_share_active_key(check.entity_id)
            if check.fair_share is not None
            else global_keys[index]
            for index, check in enumerate(checks)
        ]
        weight_keys = [
            _fair_share_weight_key(check.entity_id)
            if check.fair_share is not None
            else global_keys[index]
            for index, check in enumerate(checks)
        ]
        boost_keys = [
            fair_share_boost_key(check.entity_id, check.fair_share.organization_id)
            if check.fair_share is not None
            else global_keys[index]
            for index, check in enumerate(checks)
        ]
        denial_keys = [
            _fair_share_denial_key(check.scope, check.entity_id, window_ids[index])
            if check.fair_share is not None
            else global_keys[index]
            for index, check in enumerate(checks)
        ]
        amounts = [str(int(check.amount)) for check in checks]
        limits = [str(int(check.limit)) for check in checks]
        ttls = [str(int(check.window_seconds)) for check in checks]
        fair_enabled = ["1" if check.fair_share is not None else "0" for check in checks]
        organization_ids = [
            check.fair_share.organization_id if check.fair_share is not None else ""
            for check in checks
        ]
        weights = [
            str(max(1, int(check.fair_share.weight))) if check.fair_share is not None else "1"
            for check in checks
        ]
        thresholds = [
            str(_normalized_saturation_threshold(check.fair_share.saturation_threshold))
            if check.fair_share is not None
            else "1"
            for check in checks
        ]
        burst_multipliers = [
            str(_normalized_burst_multiplier(check.fair_share))
            if check.fair_share is not None
            else "1"
            for check in checks
        ]
        active_ttls = [
            str(max(1, int(check.fair_share.active_ttl_seconds)))
            if check.fair_share is not None
            else "1"
            for check in checks
        ]

        script = """
local n = #KEYS / 6
local now_ms = tonumber(ARGV[(9 * n) + 1]) or 0
local active_counts = {}
local share_limits = {}
local saturated_values = {}
local boost_values = {}
local total_weight_values = {}
local effective_weight_values = {}

for i = 1, n do
  local current = tonumber(redis.call('GET', KEYS[i]) or '0')
  local amount = tonumber(ARGV[i]) or 0
  local limit = tonumber(ARGV[n + i]) or 0
  if current + amount > limit then
    if ARGV[(3 * n) + i] == '1' then
      redis.call('HINCRBY', KEYS[(5 * n) + i], ARGV[(4 * n) + i], 1)
      redis.call('EXPIRE', KEYS[(5 * n) + i], tonumber(ARGV[(2 * n) + i]) or 60)
    end
    return {0, i, 1, 0, 0, 1, 1000, 0, 0, current + amount}
  end
end

for i = 1, n do
  active_counts[i] = 0
  share_limits[i] = 0
  saturated_values[i] = 0
  boost_values[i] = 1000
  total_weight_values[i] = 0
  effective_weight_values[i] = 0
  if ARGV[(3 * n) + i] == '1' then
    local org_id = ARGV[(4 * n) + i]
    local base_weight = tonumber(ARGV[(5 * n) + i]) or 1
    local threshold = tonumber(ARGV[(6 * n) + i]) or 0.8
    local burst = tonumber(ARGV[(7 * n) + i]) or 1
    local active_ttl = tonumber(ARGV[(8 * n) + i]) or 120
    local boost = tonumber(redis.call('GET', KEYS[(4 * n) + i]) or '1') or 1
    if boost < 1 then boost = 1 end
    local effective_weight = base_weight * boost
    redis.call('ZREMRANGEBYSCORE', KEYS[(2 * n) + i], '-inf', now_ms)
    redis.call('ZADD', KEYS[(2 * n) + i], now_ms + (active_ttl * 1000), org_id)
    redis.call('HSET', KEYS[(3 * n) + i], org_id, effective_weight)
    redis.call('EXPIRE', KEYS[(2 * n) + i], active_ttl)
    redis.call('EXPIRE', KEYS[(3 * n) + i], active_ttl)

    local members = redis.call('ZRANGE', KEYS[(2 * n) + i], 0, -1)
    local total_weight = 0
    for _, member in ipairs(members) do
      total_weight = total_weight + (tonumber(redis.call('HGET', KEYS[(3 * n) + i], member) or '1') or 1)
    end
    if total_weight <= 0 then total_weight = effective_weight end
    local limit = tonumber(ARGV[n + i]) or 0
    local amount = tonumber(ARGV[i]) or 0
    local current = tonumber(redis.call('GET', KEYS[i]) or '0')
    local pool_after = current + amount
    local saturated = 0
    if limit > 0 and (pool_after / limit) >= threshold then saturated = 1 end
    local share_limit = math.max(1, math.floor((limit * effective_weight / total_weight) * burst))
    local org_current = tonumber(redis.call('GET', KEYS[n + i]) or '0')

    active_counts[i] = #members
    share_limits[i] = share_limit
    saturated_values[i] = saturated
    boost_values[i] = math.floor(boost * 1000)
    total_weight_values[i] = math.floor(total_weight * 1000)
    effective_weight_values[i] = math.floor(effective_weight * 1000)

    if saturated == 1 and org_current + amount > share_limit then
      redis.call('HINCRBY', KEYS[(5 * n) + i], org_id, 1)
      redis.call('EXPIRE', KEYS[(5 * n) + i], tonumber(ARGV[(2 * n) + i]) or 60)
      return {0, i, 2, #members, share_limit, saturated, math.floor(boost * 1000), math.floor(total_weight * 1000), math.floor(effective_weight * 1000), pool_after}
    end
  end
end

local results = {1, 0}
for i = 1, n do
  local amount = tonumber(ARGV[i]) or 0
  local ttl = tonumber(ARGV[(2 * n) + i]) or 60
  local new_val = redis.call('INCRBY', KEYS[i], amount)
  redis.call('EXPIRE', KEYS[i], ttl)
  results[i + 2] = new_val
  if ARGV[(3 * n) + i] == '1' then
    redis.call('INCRBY', KEYS[n + i], amount)
    redis.call('EXPIRE', KEYS[n + i], ttl)
  end
end
for i = 1, n do results[(1 * n) + i + 2] = active_counts[i] end
for i = 1, n do results[(2 * n) + i + 2] = share_limits[i] end
for i = 1, n do results[(3 * n) + i + 2] = saturated_values[i] end
for i = 1, n do results[(4 * n) + i + 2] = boost_values[i] end
for i = 1, n do results[(5 * n) + i + 2] = total_weight_values[i] end
for i = 1, n do results[(6 * n) + i + 2] = effective_weight_values[i] end
return results
"""
        try:
            raw = await self.redis.eval(
                script,
                6 * n,
                *global_keys,
                *org_keys,
                *active_keys,
                *weight_keys,
                *boost_keys,
                *denial_keys,
                *amounts,
                *limits,
                *ttls,
                *fair_enabled,
                *organization_ids,
                *weights,
                *thresholds,
                *burst_multipliers,
                *active_ttls,
                str(int(now * 1000)),
            )
        except Exception:
            await self._handle_redis_degraded()
            return await self._check_rate_limits_fallback(
                checks,
                window_reset_at,
                per_check_resets,
            )

        ok = int(raw[0]) if isinstance(raw, (list, tuple)) and raw else 1
        if ok != 1:
            failed_index = int(raw[1]) - 1 if len(raw) >= 2 else 0
            failed_index = max(0, min(failed_index, n - 1))
            failed = checks[failed_index]
            reason = int(raw[2]) if len(raw) >= 3 else 1
            retry_after = failed.window_seconds - int(now % failed.window_seconds)
            scope = f"{failed.scope}_fair_share" if reason == 2 else failed.scope
            error = RateLimitError(
                message=f"Rate limit exceeded for scope '{scope}'",
                param=scope,
                code=f"{scope}_exceeded",
                retry_after=retry_after,
            )
            if failed.fair_share is not None and len(raw) >= 10:
                setattr(
                    error,
                    "fair_share_observation",
                    _fair_share_observation(
                        failed,
                        active_organizations=int(raw[3]),
                        share_limit=int(raw[4]),
                        saturated=bool(int(raw[5])),
                        boost_scaled=int(raw[6]),
                        total_weight_scaled=int(raw[7]),
                        effective_weight_scaled=int(raw[8]),
                        pool_current=int(raw[9]),
                    ),
                )
            raise error

        current_values = [
            _raw_int(raw, index + 2, default=check.amount)
            for index, check in enumerate(checks)
        ]
        observations: list[FairShareObservation] = []
        for index, check in enumerate(checks):
            if check.fair_share is None:
                continue
            observations.append(
                _fair_share_observation(
                    check,
                    active_organizations=_raw_int(raw, n + index + 2, default=1),
                    share_limit=_raw_int(raw, (2 * n) + index + 2, default=check.limit),
                    saturated=bool(_raw_int(raw, (3 * n) + index + 2, default=0)),
                    boost_scaled=_raw_int(raw, (4 * n) + index + 2, default=1000),
                    total_weight_scaled=_raw_int(
                        raw,
                        (5 * n) + index + 2,
                        default=max(1, check.fair_share.weight) * 1000,
                    ),
                    effective_weight_scaled=_raw_int(
                        raw,
                        (6 * n) + index + 2,
                        default=max(1, check.fair_share.weight) * 1000,
                    ),
                    pool_current=current_values[index],
                )
            )
        return RateLimitResult(
            checks=checks,
            current_values=current_values,
            window_reset_at=window_reset_at,
            window_resets=per_check_resets,
            fair_share_observations=observations,
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
        current_values: list[int] = []
        observations: list[FairShareObservation] = []

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
                current_values.append(next_value)

                fair = check.fair_share
                if fair is None:
                    continue
                active_key = _fair_share_active_key(check.entity_id)
                active = self._fallback_fair_active.setdefault(active_key, {})
                active = {
                    org_id: (active_expiry, weight)
                    for org_id, (active_expiry, weight) in active.items()
                    if active_expiry > now
                }
                effective_weight = float(max(1, int(fair.weight)))
                active[fair.organization_id] = (
                    now + max(1, int(fair.active_ttl_seconds)),
                    effective_weight,
                )
                self._fallback_fair_active[active_key] = active
                total_weight = sum(weight for _, weight in active.values()) or effective_weight
                threshold = _normalized_saturation_threshold(fair.saturation_threshold)
                saturated = next_value / check.limit >= threshold
                share_limit = max(
                    1,
                    math.floor(
                        check.limit
                        * effective_weight
                        / total_weight
                        * _normalized_burst_multiplier(fair)
                    ),
                )
                org_key = _fair_share_org_usage_fallback_key(check, window_id)
                org_expiry, org_current = self._fallback_counters.get(
                    org_key,
                    (now + ws, 0),
                )
                if org_expiry <= now:
                    org_expiry, org_current = now + ws, 0
                org_next = org_current + check.amount
                observation = FairShareObservation(
                    scope=check.scope,
                    entity_id=check.entity_id,
                    organization_id=fair.organization_id,
                    tier_key=fair.tier_key,
                    active_organizations=len(active),
                    effective_weight=effective_weight,
                    total_active_weight=total_weight,
                    share_limit=share_limit,
                    pool_limit=check.limit,
                    pool_current=next_value,
                    saturated=saturated,
                )
                if saturated and org_next > share_limit:
                    scope = f"{check.scope}_fair_share"
                    error = RateLimitError(
                        message=f"Rate limit exceeded for scope '{scope}'",
                        param=scope,
                        code=f"{scope}_exceeded",
                        retry_after=max(1, org_expiry - now),
                    )
                    setattr(error, "fair_share_observation", observation)
                    raise error
                pending_updates.append((org_key, org_expiry, org_next))
                observations.append(observation)

            for key, expiry, next_value in pending_updates:
                self._fallback_counters[key] = (expiry, next_value)

        return RateLimitResult(
            checks=normalized,
            current_values=current_values,
            window_reset_at=window_reset_at,
            window_resets=per_check_resets,
            fair_share_observations=observations,
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


def fair_share_boost_key(entity_id: str, organization_id: str) -> str:
    return f"tier_capacity_boost:{entity_id}:{organization_id}"


def fair_share_active_key(entity_id: str) -> str:
    return _fair_share_active_key(entity_id)


def fair_share_weight_key(entity_id: str) -> str:
    return _fair_share_weight_key(entity_id)


def fair_share_denial_key(scope: str, entity_id: str, window_id: int) -> str:
    return _fair_share_denial_key(scope, entity_id, window_id)


def fair_share_org_usage_key(check: RateLimitCheck, window_id: int) -> str:
    return _fair_share_org_usage_key(check, window_id)


def _fair_share_active_key(entity_id: str) -> str:
    return f"tier_capacity_active:{entity_id}"


def _fair_share_weight_key(entity_id: str) -> str:
    return f"tier_capacity_weight:{entity_id}"


def _fair_share_denial_key(scope: str, entity_id: str, window_id: int) -> str:
    return f"tier_capacity_denials:{scope}:{entity_id}:{window_id}"


def _fair_share_org_usage_key(check: RateLimitCheck, window_id: int) -> str:
    fair = check.fair_share
    organization_id = fair.organization_id if fair is not None else "unknown"
    return f"ratelimit:{check.scope}_org:{check.entity_id}:{organization_id}:{window_id}"


def _fair_share_org_usage_fallback_key(check: RateLimitCheck, window_id: int) -> str:
    fair = check.fair_share
    organization_id = fair.organization_id if fair is not None else "unknown"
    return f"{check.scope}_org:{check.entity_id}:{organization_id}:{window_id}"


def _normalized_saturation_threshold(value: object) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return 0.8
    if normalized <= 0 or normalized > 1:
        return 0.8
    return normalized


def _normalized_burst_multiplier(fair: FairShareLimit) -> float:
    if fair.strategy != "reserved_burst":
        return 1.0
    try:
        return max(1.0, float(fair.burst_multiplier))
    except (TypeError, ValueError):
        return 1.0


def _fair_share_observation(
    check: RateLimitCheck,
    *,
    active_organizations: int,
    share_limit: int,
    saturated: bool,
    boost_scaled: int,
    total_weight_scaled: int,
    effective_weight_scaled: int,
    pool_current: int,
) -> FairShareObservation:
    fair = check.fair_share
    if fair is None:
        raise ValueError("fair-share observation requires fair-share metadata")
    return FairShareObservation(
        scope=check.scope,
        entity_id=check.entity_id,
        organization_id=fair.organization_id,
        tier_key=fair.tier_key,
        active_organizations=max(1, int(active_organizations)),
        effective_weight=max(0.001, effective_weight_scaled / 1000),
        total_active_weight=max(0.001, total_weight_scaled / 1000),
        share_limit=max(1, int(share_limit)),
        pool_limit=int(check.limit),
        pool_current=max(0, int(pool_current)),
        saturated=bool(saturated),
        capacity_boost_multiplier=max(1.0, boost_scaled / 1000),
    )


def _raw_int(raw: object, index: int, *, default: int) -> int:
    if not isinstance(raw, (list, tuple)) or index < 0 or index >= len(raw):
        return int(default)
    try:
        return int(raw[index])
    except (TypeError, ValueError):
        return int(default)


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
