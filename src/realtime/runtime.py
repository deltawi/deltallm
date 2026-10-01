from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

from src.providers.openai_realtime import OpenAIRealtimeConnector
from src.realtime.contracts import RealtimeAdmission, RealtimeError, RealtimeLimits
from src.realtime.lifecycle import RealtimeDrain


class RealtimeRuntime:
    """Bootstrap-owned transport dependencies and a local pre-auth socket gate.

    There is deliberately no default admission implementation. Bootstrap must
    supply qualified routing, distributed limits, budgets and durable recovery
    together before exposing any billable upstream connection.
    """

    def __init__(
        self,
        *,
        admission: RealtimeAdmission,
        limits: RealtimeLimits | None = None,
        connector: OpenAIRealtimeConnector | None = None,
    ) -> None:
        if admission is None:
            raise ValueError("Realtime requires an admission owner")
        self.admission = admission
        self.limits = limits or RealtimeLimits()
        self.connector = connector or OpenAIRealtimeConnector()
        self._sessions: dict[asyncio.Task, RealtimeDrain] = {}
        self._closing = False
        self._cleanup_failed = False

    @property
    def active_sessions(self) -> int:
        return len(self._sessions)

    @contextmanager
    def reserve(self) -> Iterator[RealtimeDrain]:
        # No await or waiters: on one event loop this check and insert is an
        # atomic local gate. Distributed admission is a separate required step.
        if self._closing or len(self._sessions) >= self.limits.max_connections:
            raise RealtimeError(
                "capacity_exceeded", "Realtime connection capacity reached", close_code=1013
            )
        task = asyncio.current_task()
        if task is None or task in self._sessions:
            raise RuntimeError("Realtime sessions need a unique owner task")
        drain = RealtimeDrain(self.limits)
        self._sessions[task] = drain
        try:
            yield drain
        finally:
            self._cleanup_failed |= drain.failed
            del self._sessions[task]

    async def close(self) -> None:
        self._closing = True
        owners = dict(self._sessions)
        for task, drain in owners.items():
            if drain.deadline is None:
                drain.begin()
                task.cancel()
        if owners:
            deadline = max(drain.begin() for drain in owners.values())
            done, pending = await asyncio.wait(
                owners, timeout=max(0, deadline - asyncio.get_running_loop().time())
            )
            # Observe completed owners without cancelling cleanup that has
            # already started, including on concurrent/repeated close calls.
            for task in done:
                if not task.cancelled():
                    task.exception()
            if pending:
                self._cleanup_failed = True
                raise RuntimeError("Realtime session cleanup did not complete before shutdown")
        if self._cleanup_failed:
            raise RuntimeError("Realtime session cleanup failed")
