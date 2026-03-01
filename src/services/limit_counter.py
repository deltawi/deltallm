from __future__ import annotations

import asyncio
from dataclasses import dataclass
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

    async def check_rate_limits_atomic(self, checks: list[RateLimitCheck]) -> None:
        """Atomically validate and increment minute-bucket limits for multiple scopes."""
        if self.redis is None:
            await self._check_rate_limits_fallback(checks)
            return

        normalized = [check for check in checks if check.limit > 0 and check.amount > 0]
        if not normalized:
            return

        window_seconds = 60
        window_id = self._window_id(window_seconds)
        retry_after = window_seconds - int(time.time() % window_seconds)
        keys = [f"ratelimit:{check.scope}:{check.entity_id}:{window_id}" for check in normalized]
        limits = [str(int(check.limit)) for check in normalized]
        amounts = [str(int(check.amount)) for check in normalized]

        # First pass validates all scopes; second pass increments only if all pass.
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
for i = 1, n do
  local amount = tonumber(ARGV[i]) or 0
  redis.call('INCRBY', KEYS[i], amount)
  redis.call('EXPIRE', KEYS[i], tonumber(ARGV[(2 * n) + 1]))
end
return {1, 0}
"""
        try:
            raw = await self.redis.eval(script, len(keys), *keys, *amounts, *limits, str(window_seconds))
        except Exception:
            await self._handle_redis_degraded()
            await self._check_rate_limits_fallback(checks)
            return
        ok = int(raw[0]) if isinstance(raw, (list, tuple)) and len(raw) >= 1 else 1
        if ok == 1:
            return

        failed_index = int(raw[1]) - 1 if isinstance(raw, (list, tuple)) and len(raw) >= 2 else 0
        failed = normalized[max(0, failed_index)]
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

    async def _check_rate_limits_fallback(self, checks: list[RateLimitCheck]) -> None:
        if self.degraded_mode == "fail_closed":
            raise ServiceUnavailableError(message="Rate limit backend unavailable")

        normalized = [check for check in checks if check.limit > 0 and check.amount > 0]
        if not normalized:
            return

        window_seconds = 60
        window_id = self._window_id(window_seconds)
        now = int(time.time())
        pending_updates: list[tuple[str, int, int]] = []

        async with self._fallback_lock:
            for check in normalized:
                key = f"{check.scope}:{check.entity_id}:{window_id}"
                expiry, current = self._fallback_counters.get(key, (now + window_seconds, 0))
                if expiry <= now:
                    expiry, current = now + window_seconds, 0
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

            for key, expiry, next_value in pending_updates:
                self._fallback_counters[key] = (expiry, next_value)

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
