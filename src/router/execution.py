from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Generic, TypeVar

from src.models.errors import TimeoutError
from src.router.router import Deployment


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RequestDeadline:
    """One monotonic budget shared by planning, retries, and provider work."""

    expires_at: float

    @classmethod
    def after(cls, timeout_seconds: float) -> RequestDeadline:
        return cls(asyncio.get_running_loop().time() + timeout_seconds)

    def remaining(self) -> float:
        return max(0.0, self.expires_at - asyncio.get_running_loop().time())

    def require_remaining(self) -> float:
        remaining = self.remaining()
        if remaining <= 0:
            raise TimeoutError(message="Request deadline exceeded")
        return remaining

    async def wait_for(self, awaitable: Awaitable[T], *, limit: float | None = None) -> T:
        try:
            remaining = self.require_remaining()
        except BaseException:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise
        timeout = remaining if limit is None else min(remaining, limit)
        try:
            return await asyncio.wait_for(awaitable, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(message="Request deadline exceeded") from exc


@dataclass(slots=True)
class ManagedFailoverResult(Generic[T]):
    """A successful attempt whose capacity permit is owned by the caller."""

    value: T
    deployment: Deployment
    deadline: RequestDeadline
    _release: Callable[[], Awaitable[None]] = field(repr=False)
    _released: bool = field(default=False, init=False, repr=False)
    _release_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def release(self) -> None:
        async with self._release_lock:
            if self._released:
                return
            await self._release()
            self._released = True
