from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any

from src.models.errors import RateLimitError


@dataclass(frozen=True)
class RateLimitCheck:
    scope: str
    entity_id: str
    limit: int
    amount: int = 1


class LimitCounter:
    def __init__(self, redis_client: Any | None = None) -> None:
        self.redis = redis_client

    @staticmethod
    def _window_id(window_seconds: int) -> int:
        return math.floor(time.time() / window_seconds)

    async def check_rate_limit(self, scope: str, entity_id: str, limit: int | None, amount: int = 1) -> None:
        if limit is None or limit <= 0 or self.redis is None:
            return

        window_seconds = 60
        window_id = self._window_id(window_seconds)
        key = f"ratelimit:{scope}:{entity_id}:{window_id}"

        current = await self.redis.incrby(key, amount)
        if current == amount:
            await self.redis.expire(key, window_seconds)

        if current > limit:
            retry_after = window_seconds - int(time.time() % window_seconds)
            raise RateLimitError(retry_after=retry_after)

    async def check_rate_limits_atomic(self, checks: list[RateLimitCheck]) -> None:
        """Atomically validate and increment minute-bucket limits for multiple scopes."""
        if self.redis is None:
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
        raw = await self.redis.eval(script, len(keys), *keys, *amounts, *limits, str(window_seconds))
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
        if limit is None or limit <= 0 or self.redis is None:
            return

        key = f"parallel:{scope}:{entity_id}"
        current = await self.redis.incr(key)
        if current == 1:
            await self.redis.expire(key, 300)
        if current > limit:
            await self.redis.decr(key)
            raise RateLimitError(message="Parallel request limit exceeded", retry_after=1)

    async def release_parallel(self, scope: str, entity_id: str) -> None:
        if self.redis is None:
            return
        key = f"parallel:{scope}:{entity_id}"
        await self.redis.decr(key)
