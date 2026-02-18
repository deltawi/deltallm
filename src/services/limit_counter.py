from __future__ import annotations

import math
import time
from typing import Any

from src.models.errors import RateLimitError


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
