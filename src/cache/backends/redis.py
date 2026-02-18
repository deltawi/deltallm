from __future__ import annotations

import json
import logging
from dataclasses import asdict

from redis.asyncio import Redis

from .base import CacheBackend, CacheEntry

logger = logging.getLogger(__name__)


class RedisBackend(CacheBackend):
    def __init__(self, redis_client: Redis) -> None:
        self.redis = redis_client

    @staticmethod
    def _cache_key(key: str) -> str:
        return f"cache:{key}"

    async def get(self, key: str) -> CacheEntry | None:
        try:
            raw = await self.redis.get(self._cache_key(key))
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning("cache redis get failed: %s", exc)
            return None

        if raw is None:
            return None

        if isinstance(raw, bytes):
            payload = json.loads(raw.decode("utf-8"))
        else:
            payload = json.loads(raw)

        return CacheEntry(
            response=payload.get("response", {}),
            model=payload.get("model", "unknown"),
            cached_at=float(payload.get("cached_at", 0)),
            ttl=int(payload.get("ttl", 0)),
            token_count=int(payload.get("token_count", 0)),
        )

    async def set(self, key: str, entry: CacheEntry, ttl: int | None = None) -> None:
        data = json.dumps(asdict(entry), separators=(",", ":"))
        effective_ttl = int(ttl or entry.ttl)
        try:
            await self.redis.setex(self._cache_key(key), effective_ttl, data)
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning("cache redis set failed: %s", exc)

    async def delete(self, key: str) -> None:
        await self.redis.delete(self._cache_key(key))

    async def clear(self) -> None:
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor=cursor, match="cache:*")
            if keys:
                await self.redis.delete(*keys)
            if cursor == 0:
                break
