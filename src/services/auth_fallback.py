"""Bounded ownership of cold auth lookups; caller timeouts never free live SQL."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter

from src.concurrency import BoundedCapacityGate, CapacityGateFull, CapacityGateTimedOut
from src.metrics.admission import auth_callers, auth_events, auth_tasks, auth_seconds
from src.models.errors import AuthenticationUnavailableError
from src.models.responses import UserAPIKeyAuth
from src.telemetry.lifecycle import stop_tasks_before_deadline


@dataclass(frozen=True)
class AuthFallbackLimits:
    max_active: int = 8
    max_waiters: int = 32
    queue_timeout_ms: int = 10
    timeout_seconds: float = 0.5
    cache_timeout_seconds: float = 0.1
    cache_max_bytes: int = 131_072


@dataclass
class AuthLookup:
    deadline: float
    invalidated: bool = False

    def check(self) -> None:
        if self.invalidated or asyncio.get_running_loop().time() >= self.deadline:
            raise AuthenticationUnavailableError()


@dataclass
class _Flight:
    lookup: AuthLookup
    task: asyncio.Task[UserAPIKeyAuth]


class AuthFallback:
    def __init__(self, limits: AuthFallbackLimits) -> None:
        self.limits = limits
        self.gate = BoundedCapacityGate(
            concurrency=limits.max_active, max_waiters=limits.max_waiters
        )
        self._flights: dict[str, _Flight] = {}
        self.callers = 0
        self.closed = False

    @property
    def size(self) -> int:
        return len(self._flights)

    async def run(
        self, token_hash: str, factory: Callable[[AuthLookup], Awaitable[UserAPIKeyAuth]]
    ) -> UserAPIKeyAuth:
        if self.closed:
            self._reject("closed")
        capacity = self.limits.max_active + self.limits.max_waiters
        if self.callers >= capacity:
            self._reject("overloaded")
        flight = self._flights.get(token_hash)
        if flight is None:
            if self.size >= capacity:
                self._reject("overloaded")
            lookup = AuthLookup(asyncio.get_running_loop().time() + self.limits.timeout_seconds)
            task = asyncio.create_task(self._execute(lookup, factory), name="auth-fallback")
            flight = _Flight(lookup, task)
            self._flights[token_hash] = flight
            auth_tasks.inc()
            task.add_done_callback(lambda done: self._completed(token_hash, done))
        else:
            auth_events.labels("lookup", "coalesced").inc()
        flight.lookup.check()
        self.callers += 1
        auth_callers.inc()
        started = perf_counter()
        try:
            async with asyncio.timeout_at(flight.lookup.deadline):
                result = await asyncio.shield(flight.task)
            flight.lookup.check()
            # Request middleware mutates auth metadata and scope. Never share it.
            return result.model_copy(deep=True)
        except TimeoutError:
            self._reject("deadline")
        finally:
            self.callers -= 1
            auth_callers.dec()
            auth_seconds.labels("caller").observe(perf_counter() - started)

    async def _execute(
        self, lookup: AuthLookup, factory: Callable[[AuthLookup], Awaitable[UserAPIKeyAuth]]
    ) -> UserAPIKeyAuth:
        try:
            await self.gate.acquire(timeout_seconds=self.limits.queue_timeout_ms / 1000)
        except (CapacityGateFull, CapacityGateTimedOut):
            self._reject("queue_full")
        started = perf_counter()
        try:
            lookup.check()
            # The caller has a short deadline. The task stays owned if native
            # SQL outlives that deadline; freeing it early would amplify fallback.
            result = await factory(lookup)
            lookup.check()
            auth_events.labels("lookup", "completed").inc()
            return result
        finally:
            await self.gate.release()
            auth_seconds.labels("execution").observe(perf_counter() - started)

    def invalidate(self, token_hash: str | None = None) -> None:
        if token_hash is None:
            for flight in self._flights.values():
                flight.lookup.invalidated = True
        elif (flight := self._flights.get(token_hash)) is not None:
            flight.lookup.invalidated = True

    async def close(self) -> None:
        self.closed = True
        self.invalidate()
        await stop_tasks_before_deadline(
            [flight.task for flight in self._flights.values()],
            deadline=asyncio.get_running_loop().time() + 1.0,
            cancel_first=True,
        )
        # Pending cancellation-resistant work remains registered/observed until
        # the centrally owned clients close. Closed admission cannot replace it.

    def _completed(self, token_hash: str, task: asyncio.Task[UserAPIKeyAuth]) -> None:
        flight = self._flights.get(token_hash)
        if flight is not None and flight.task is task:
            self._flights.pop(token_hash)
            auth_tasks.dec()
        if not task.cancelled() and task.exception() is not None:
            auth_events.labels("lookup", "failed").inc()

    @staticmethod
    def _reject(outcome: str) -> None:
        auth_events.labels("admission", outcome).inc()
        raise AuthenticationUnavailableError()
