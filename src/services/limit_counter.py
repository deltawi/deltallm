from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import math
import time
from typing import Any

from src.models.errors import RateLimitError, ServiceUnavailableError


@dataclass(frozen=True)
class RateLimitCheck:
    scope: str
    entity_id: str
    limit: int
    amount: int = 1
    window_seconds: int = 60


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
        if limit is None or limit <= 0:
            return
        if self.redis is None:
            await self._acquire_parallel_fallback(scope, entity_id, limit)
            return

        key = f"parallel:{scope}:{entity_id}"
        try:
            current = await self.redis.incr(key)
        except Exception:
            await self._handle_redis_degraded()
            await self._acquire_parallel_fallback(scope, entity_id, limit)
            return
        if current == 1:
            try:
                await self.redis.expire(key, 300)
            except Exception:
                await self._handle_redis_degraded()
        if current > limit:
            try:
                await self.redis.decr(key)
            except Exception:
                await self._handle_redis_degraded()
            raise RateLimitError(message="Parallel request limit exceeded", retry_after=1)

    async def release_parallel(self, scope: str, entity_id: str) -> None:
        if self.redis is None:
            await self._release_parallel_fallback(scope, entity_id)
            return
        key = f"parallel:{scope}:{entity_id}"
        try:
            await self.redis.decr(key)
        except Exception:
            await self._handle_redis_degraded()
            await self._release_parallel_fallback(scope, entity_id)

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
            raise RateLimitError(message="Parallel request limit exceeded", retry_after=1)

    async def _release_parallel_fallback(self, scope: str, entity_id: str) -> None:
        key = f"{scope}:{entity_id}"
        async with self._fallback_lock:
            current = max(0, int(self._fallback_parallel.get(key, 0)) - 1)
            if current == 0:
                self._fallback_parallel.pop(key, None)
            else:
                self._fallback_parallel[key] = current
