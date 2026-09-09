from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
import math

from src.models.errors import TimeoutError as RequestTimeoutError
from src.router.execution import RequestDeadline
from src.router.selection.contracts import (
    SELECTOR_MAX_JOINERS,
    SelectorDecision,
    SelectorInvariantError,
    SelectorJoinLimitError,
    SelectorOperationAbortedError,
    SelectorUsage,
    UnattemptedSelectorUsage,
)


class SelectorState(StrEnum):
    NEW = "new"
    RUNNING = "running"
    DECIDED = "decided"
    ABORTED = "aborted"


class _AbortCause(StrEnum):
    CANCELLED = "cancelled"
    DEADLINE = "deadline"
    FAILURE = "failure"


class RequestSelectorState:
    """One disposable outer-operation state; never a process or distributed cache."""

    __slots__ = ("_deadline", "_state", "_decision", "_done", "_abort", "_joiners", "_usage")

    def __init__(self, deadline: RequestDeadline) -> None:
        if type(deadline.expires_at) not in (int, float) or not math.isfinite(deadline.expires_at):
            raise SelectorInvariantError()
        self._deadline = deadline
        self._state = SelectorState.NEW
        self._decision: SelectorDecision | None = None
        self._done: asyncio.Future[None] | None = None
        self._abort = _AbortCause.FAILURE
        self._joiners = 0
        self._usage: SelectorUsage = UnattemptedSelectorUsage()

    @property
    def deadline(self) -> RequestDeadline:
        return self._deadline

    @property
    def state(self) -> SelectorState:
        return self._state

    @property
    def usage(self) -> SelectorUsage:
        return self._usage

    def decision_for_planning(self) -> SelectorDecision | None:
        """Read the single operation decision without starting or joining provider work."""
        if self._state in (SelectorState.NEW, SelectorState.RUNNING):
            self._deadline.require_remaining()
            return None
        return self._terminal_result()

    def observe_usage(self, usage: SelectorUsage) -> None:
        if self._state is not SelectorState.RUNNING:
            raise SelectorInvariantError()
        self._usage = usage

    async def select_once(
        self, produce: Callable[[], Awaitable[SelectorDecision]]
    ) -> SelectorDecision:
        if self._state is SelectorState.RUNNING:
            await self._join()
            return self._terminal_result()
        if self._state is not SelectorState.NEW:
            return self._terminal_result()
        self._state = SelectorState.RUNNING
        self._done = asyncio.get_running_loop().create_future()
        try:
            self._deadline.require_remaining()
            result = await produce()
            self._deadline.require_remaining()
            self._decision = result
            self._usage = result.usage
            self._state = SelectorState.DECIDED
            return result
        except asyncio.CancelledError:
            self._state, self._abort = SelectorState.ABORTED, _AbortCause.CANCELLED
            raise
        except RequestTimeoutError:
            self._state, self._abort = SelectorState.ABORTED, _AbortCause.DEADLINE
            raise
        except BaseException:
            # Lifecycle boundary: wake joiners without retaining exception tracebacks/content.
            self._state, self._abort = SelectorState.ABORTED, _AbortCause.FAILURE
            raise
        finally:
            self._done.set_result(None)

    async def _join(self) -> None:
        if self._joiners >= SELECTOR_MAX_JOINERS:
            raise SelectorJoinLimitError()
        if self._done is None:
            raise SelectorInvariantError()
        self._joiners += 1
        try:
            async with asyncio.timeout_at(self._deadline.expires_at):
                await asyncio.shield(self._done)
        except TimeoutError:
            raise RequestTimeoutError(message="Request deadline exceeded") from None
        finally:
            self._joiners -= 1

    def _terminal_result(self) -> SelectorDecision:
        if self._state is SelectorState.ABORTED:
            if self._abort is _AbortCause.CANCELLED:
                raise asyncio.CancelledError()
            if self._abort is _AbortCause.DEADLINE:
                raise RequestTimeoutError(message="Request deadline exceeded")
            raise SelectorOperationAbortedError()
        self._deadline.require_remaining()
        if self._state is not SelectorState.DECIDED or self._decision is None:
            raise SelectorInvariantError()
        return self._decision
