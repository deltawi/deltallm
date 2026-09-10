from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import AsyncExitStack, asynccontextmanager

from src.batch.worker_types import BatchItemLeaseLostError
from src.router.attempt_capacity import AttemptSlotAdmission
from src.router.execution import RequestDeadline
from src.router.router import Deployment


class ChatItemLeaseWatch:
    """One existing heartbeat, borrowed by split execution and closed by its chunk."""

    def __init__(
        self,
        task: asyncio.Task[None],
        lost: asyncio.Event,
        stop: Callable[[asyncio.Task[None]], Awaitable[None]],
    ) -> None:
        self._task: asyncio.Task[None] | None = task
        self.lost = lost
        self._stop = stop

    async def stop(self) -> None:
        task = self._task
        if task is not None:
            try:
                await self._stop(task)
            finally:
                if task.done():
                    self._task = None


async def stop_chat_watches(watches: Iterable[ChatItemLeaseWatch]) -> None:
    # ExitStack attempts every callback even if stopping one watch fails.
    async with AsyncExitStack() as cleanup:
        for watch in watches:
            cleanup.push_async_callback(watch.stop)


class ClaimedChatAttemptCapacity(AttemptSlotAdmission):
    """Borrow shared answer capacity, then fence split dispatch before provider I/O."""

    def __init__(
        self,
        capacity: AttemptSlotAdmission,
        watch: ChatItemLeaseWatch,
        renew: Callable[[float], Awaitable[bool]],
    ) -> None:
        self._capacity, self._watch, self._renew = capacity, watch, renew

    @asynccontextmanager
    async def slot(self, deployment: Deployment, deadline: RequestDeadline) -> AsyncIterator[None]:
        async with self._capacity.slot(deployment, deadline):
            if not self._watch.lost.is_set():
                try:
                    owned = await self._renew(deadline.expires_at)
                except Exception:
                    # This integration boundary denies ambiguous ownership, not provider health.
                    owned = False
                if owned and not self._watch.lost.is_set():
                    yield
                    return
            self._watch.lost.set()
            raise BatchItemLeaseLostError("Batch item lease lost before split dispatch")
