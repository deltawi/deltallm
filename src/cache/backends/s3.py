from __future__ import annotations

from .base import CacheBackend, CacheEntry


class S3Backend(CacheBackend):
    """Phase 3 P1 stub for future persistent cache support."""

    async def get(self, key: str) -> CacheEntry | None:  # pragma: no cover - stub
        raise NotImplementedError("S3 backend is not implemented yet")

    async def set(self, key: str, entry: CacheEntry, ttl: int | None = None) -> None:  # pragma: no cover - stub
        raise NotImplementedError("S3 backend is not implemented yet")

    async def delete(self, key: str) -> None:  # pragma: no cover - stub
        raise NotImplementedError("S3 backend is not implemented yet")

    async def clear(self) -> None:  # pragma: no cover - stub
        raise NotImplementedError("S3 backend is not implemented yet")
