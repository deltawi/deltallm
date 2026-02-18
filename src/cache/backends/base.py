from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheEntry:
    response: dict[str, Any]
    model: str
    cached_at: float
    ttl: int
    token_count: int = 0


class CacheBackend(ABC):
    @abstractmethod
    async def get(self, key: str) -> CacheEntry | None:
        raise NotImplementedError

    @abstractmethod
    async def set(self, key: str, entry: CacheEntry, ttl: int | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def clear(self) -> None:
        raise NotImplementedError
