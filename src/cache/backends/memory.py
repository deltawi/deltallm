from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

from .base import CacheBackend, CacheEntry


class InMemoryBackend(CacheBackend):
    def __init__(self, max_size: int = 10_000) -> None:
        self.max_size = max_size
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> CacheEntry | None:
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None

            if time.time() > entry.cached_at + entry.ttl:
                self._cache.pop(key, None)
                return None

            self._cache.move_to_end(key)
            return entry

    async def set(self, key: str, entry: CacheEntry, ttl: int | None = None) -> None:
        async with self._lock:
            if ttl is not None:
                entry = CacheEntry(
                    response=entry.response,
                    model=entry.model,
                    cached_at=entry.cached_at,
                    ttl=ttl,
                    token_count=entry.token_count,
                )

            if key in self._cache:
                self._cache.move_to_end(key)

            self._cache[key] = entry
            while len(self._cache) > self.max_size:
                self._cache.popitem(last=False)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._cache.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._cache.clear()
