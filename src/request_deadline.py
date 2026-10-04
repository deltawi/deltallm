"""One monotonic inference budget, independent of HTTP and routing ownership."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Awaitable, TypeVar

from src.models.errors import RoutingFailureAction, TimeoutError

T = TypeVar("T")


def request_timeout_error() -> TimeoutError:
    return TimeoutError(
        message="Request deadline exceeded",
        code="request_deadline_exceeded",
        affects_deployment_health=False,
        routing_failure_action=RoutingFailureAction.FAIL_FAST,
    )


@dataclass(frozen=True, slots=True)
class RequestDeadline:
    """One monotonic budget shared by planning, retries, and provider work."""

    expires_at: float
    started_at: float | None = None

    @classmethod
    def after(cls, timeout_seconds: float) -> RequestDeadline:
        started = asyncio.get_running_loop().time()
        return cls(started + timeout_seconds, started_at=started)

    def remaining(self) -> float:
        return max(0.0, self.expires_at - asyncio.get_running_loop().time())

    def require_remaining(self) -> float:
        remaining = self.remaining()
        if remaining <= 0:
            raise request_timeout_error()
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
            if self.remaining() <= 0:
                raise request_timeout_error() from exc
            # A shorter attempt limit can still describe a provider timeout.
            raise TimeoutError(message="Request deadline exceeded") from exc


@dataclass(slots=True)
class RequestBudget:
    """Request-owned mutable limit; each published deadline value is immutable."""

    deadline: RequestDeadline
    reschedule: Callable[[float], None] | None = None

    def constrain(self, timeout_seconds: float) -> RequestDeadline:
        origin = self.deadline.started_at
        if origin is None:
            origin = asyncio.get_running_loop().time()
        expires = min(self.deadline.expires_at, origin + timeout_seconds)
        if expires < self.deadline.expires_at:
            self.deadline = RequestDeadline(expires, started_at=origin)
            if self.reschedule is not None:
                self.reschedule(expires)
        return self.deadline


_CURRENT_BUDGET: ContextVar[RequestBudget | None] = ContextVar("request_budget", default=None)


def current_request_deadline() -> RequestDeadline | None:
    budget = _CURRENT_BUDGET.get()
    return budget.deadline if budget is not None else None


@contextmanager
def bind_request_deadline(
    deadline: RequestDeadline, *, reschedule: Callable[[float], None] | None = None
) -> Iterator[RequestBudget]:
    budget = RequestBudget(deadline, reschedule)
    token = _CURRENT_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _CURRENT_BUDGET.reset(token)


def inherited_request_deadline(timeout_seconds: float) -> RequestDeadline:
    budget = _CURRENT_BUDGET.get()
    if budget is None:
        return RequestDeadline.after(timeout_seconds)
    return budget.constrain(timeout_seconds)
