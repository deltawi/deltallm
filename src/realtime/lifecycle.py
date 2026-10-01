from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from types import TracebackType
from typing import AsyncContextManager, TypeVar

from src.realtime.contracts import RealtimeLimits

T = TypeVar("T")


class RealtimeDrain:
    """One owner deadline for pumps/resources followed by the downstream close.

    The total budget is the existing cleanup allowance plus the final write
    allowance. Beginning another phase or shutdown call never resets it.
    """

    def __init__(self, limits: RealtimeLimits) -> None:
        self.limits = limits
        self.deadline: float | None = None
        self.cleanup_deadline: float | None = None
        self.failed = False

    def begin(self) -> float:
        if self.deadline is None:
            self.cleanup_deadline = asyncio.get_running_loop().time() + self.limits.cleanup_seconds
            self.deadline = self.cleanup_deadline + self.limits.write_seconds
        return self.deadline

    async def enter_context(self, stack: AsyncExitStack, context: AsyncContextManager[T]) -> T:
        resource = await context.__aenter__()

        async def close(
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> bool | None:
            # Bound each exit separately by the same deadline. ExitStack
            # continues to admission finalization when upstream close fails;
            # that must not consume a fresh cleanup allowance.
            try:
                async with self.cleanup(propagating=exc):
                    return await context.__aexit__(exc_type, exc, traceback)
            except BaseException as failure:
                # Some contexts re-raise the incoming session exception.
                # A new failure, including cancellation during the exit,
                # means finalization did not complete.
                if failure is not exc:
                    self.failed = True
                raise

        stack.push_async_exit(close)
        return resource

    @asynccontextmanager
    async def cleanup(self, *, propagating: BaseException | None = None) -> AsyncIterator[None]:
        self.begin()
        async with self._bounded(self.cleanup_deadline, propagating=propagating):
            yield

    @asynccontextmanager
    async def close_socket(self) -> AsyncIterator[None]:
        deadline = min(self.begin(), asyncio.get_running_loop().time() + self.limits.write_seconds)
        async with self._bounded(deadline):
            yield

    @asynccontextmanager
    async def _bounded(
        self, deadline: float | None, *, propagating: BaseException | None = None
    ) -> AsyncIterator[None]:
        try:
            async with asyncio.timeout_at(deadline) as timeout:
                yield
            if timeout.expired():
                raise TimeoutError
        except TimeoutError as failure:
            if failure is not propagating or timeout.expired():
                self.failed = True
            raise
