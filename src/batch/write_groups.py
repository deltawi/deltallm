"""Send batch writes that would wait for a database slot together.

A write that finds a free slot runs alone, exactly as it always did. Writes
that arrive while every slot is busy wait in order. When a slot frees, the
oldest waiting write goes out together with the waiting writes behind it that
fit, as one statement. So a database that keeps up sees the same writes as
before, and a busy one sees fewer, larger transactions instead of a queue of
small ones.

A group either applies to every member or to none. When a group is not
accepted, or fails, each member is written again alone, ahead of newer writes,
so every member ends with exactly the answer (or error) it gets on its own. One
member's bad row can never fail the others.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from src.metrics import observe_batch_write_group_size

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


@dataclass(slots=True, eq=False)
class _Waiting(Generic[T]):
    request: T
    rows: int
    size: int
    # Set when its group was not accepted: written alone from then on.
    alone: bool
    future: asyncio.Future[object] = field(repr=False)


@dataclass(frozen=True, slots=True)
class _Done(Generic[R]):
    value: R


# A slot handed to one waiting write, which then writes alone in its own task.
_SLOT = object()


class WriteGroups(Generic[T, R]):
    """At most ``limit`` writes at once; writes that would wait share one."""

    def __init__(
        self,
        *,
        kind: str,
        limit: int,
        write: Callable[[Sequence[T]], Awaitable[R]],
        accepted: Callable[[R], bool],
        max_rows: int,
        rows: Callable[[T], int],
        size: Callable[[T], int] | None = None,
        max_size: int | None = None,
        conflicts: Callable[[Sequence[T], T], bool] | None = None,
        enabled: bool = True,
    ) -> None:
        if type(limit) is not int or limit < 1 or max_rows < 1:
            raise ValueError("Invalid batch write group limits")
        self._kind = kind
        self._limit = limit
        self._write = write
        self._accepted = accepted
        self._max_rows = max_rows
        self._rows = rows
        self._size = size
        self._max_size = max_size
        self._conflicts = conflicts
        # Off: waiting writes still go out one at a time, in order.
        self._enabled = enabled
        self._active = 0
        self._waiting: deque[_Waiting[T]] = deque()
        self._groups: set[asyncio.Task[None]] = set()

    @property
    def active(self) -> int:
        return self._active

    @property
    def waiting(self) -> int:
        return sum(1 for waiting in self._waiting if not waiting.future.done())

    async def run(self, request: T) -> R:
        if self._active < self._limit and not self._waiting:
            self._active += 1
            return await self._write_alone(request)
        waiting: _Waiting[T] = _Waiting(
            request=request,
            rows=max(1, int(self._rows(request))),
            size=0 if self._size is None else max(0, int(self._size(request))),
            alone=not self._enabled,
            future=asyncio.get_running_loop().create_future(),
        )
        self._waiting.append(waiting)
        self._dispatch()
        try:
            outcome = await waiting.future
        except asyncio.CancelledError:
            future = waiting.future
            if future.done() and not future.cancelled() and future.result() is _SLOT:
                # Handed a slot just before the cancel: give it back.
                self._release()
            elif waiting in self._waiting:
                self._waiting.remove(waiting)
            # Already in a group: it is written anyway, like a write cancelled
            # after its statement left.
            raise
        if outcome is _SLOT:
            return await self._write_alone(request)
        assert isinstance(outcome, _Done)
        return outcome.value

    async def _write_alone(self, request: T) -> R:
        # Holds one counted slot; runs in the caller's task, as before.
        try:
            observe_batch_write_group_size(kind=self._kind, writes=1)
            return await self._write((request,))
        finally:
            self._release()

    def _release(self) -> None:
        self._active -= 1
        self._dispatch()

    def _dispatch(self) -> None:
        while self._waiting and self._active < self._limit:
            group = self._take_group()
            if not group:
                return
            self._active += 1
            if len(group) == 1:
                group[0].future.set_result(_SLOT)
                continue
            task = asyncio.get_running_loop().create_task(
                self._write_group(group), name=f"batch-write-group:{self._kind}"
            )
            # The group belongs to no single member, so cancelling one member
            # never cancels the statement the others are waiting on.
            self._groups.add(task)
            task.add_done_callback(self._groups.discard)

    def _take_group(self) -> list[_Waiting[T]]:
        head: _Waiting[T] | None = None
        while self._waiting:
            candidate = self._waiting.popleft()
            if not candidate.future.done():
                head = candidate
                break
        if head is None:
            return []
        group = [head]
        if head.alone:
            return group
        rows, size = head.rows, head.size
        left: deque[_Waiting[T]] = deque()
        for waiting in self._waiting:
            if waiting.future.done():
                continue
            if (
                waiting.alone
                or rows + waiting.rows > self._max_rows
                or (self._max_size is not None and size + waiting.size > self._max_size)
                or (
                    self._conflicts is not None
                    and self._conflicts([member.request for member in group], waiting.request)
                )
            ):
                left.append(waiting)
                continue
            group.append(waiting)
            rows += waiting.rows
            size += waiting.size
        self._waiting = left
        return group

    async def _write_group(self, group: list[_Waiting[T]]) -> None:
        outcome: object | None = None
        try:
            observe_batch_write_group_size(kind=self._kind, writes=len(group))
            try:
                value = await self._write([member.request for member in group])
            except Exception:
                # An error says nothing reliable about each member: it may be
                # one member's row, or the database. Each finds out alone.
                logger.debug(
                    "batch write group failed; writing members alone kind=%s size=%s",
                    self._kind,
                    len(group),
                    exc_info=True,
                )
                accepted = False
            else:
                accepted = self._accepted(value)
            if accepted:
                outcome = _Done(value)
            else:
                # Not applied: write each member again alone, ahead of newer
                # writes, so each gets its own exact answer.
                for member in reversed(group):
                    member.alone = True
                    self._waiting.appendleft(member)
        except BaseException:
            for member in group:
                if not member.future.done():
                    member.future.cancel()
            raise
        finally:
            self._release()
        if outcome is None:
            return
        for member in group:
            if not member.future.done():
                member.future.set_result(outcome)
