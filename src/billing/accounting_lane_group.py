"""A fixed group owns bounded accounting lanes and their existing task lifecycles."""

from __future__ import annotations

import asyncio
from typing import Protocol

from src.telemetry.lifecycle import WorkerHealth, WorkerState


class AccountingLane(Protocol):
    @property
    def task(self) -> asyncio.Task[None] | None: ...

    @property
    def worker_health(self) -> WorkerHealth: ...

    @property
    def retained_claim_bytes(self) -> int: ...

    def stop_claims(self) -> None: ...

    async def start(self, *, expires_at: float) -> None: ...

    async def close(self, *, expires_at: float) -> bool: ...


class AccountingLaneGroup:
    """Keep each existing fenced worker independent, with at most four lanes."""

    def __init__(self, lanes: tuple[AccountingLane, ...]) -> None:
        if not 1 <= len(lanes) <= 4 or len({id(lane) for lane in lanes}) != len(lanes):
            raise ValueError("Accounting groups require one to four distinct lanes")
        self.lanes = lanes
        self._started = False
        self._closed = False

    @property
    def tasks(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(lane.task for lane in self.lanes if lane.task is not None)

    @property
    def retained_claim_bytes(self) -> int:
        return sum(lane.retained_claim_bytes for lane in self.lanes)

    @property
    def worker_health(self) -> WorkerHealth:
        for lane in self.lanes:
            health = lane.worker_health
            if health.state is not WorkerState.READY:
                return health
        return WorkerHealth(WorkerState.READY)

    def stop_claims(self) -> None:
        for lane in self.lanes:
            lane.stop_claims()

    async def start(self, *, expires_at: float) -> None:
        if self._started or self._closed:
            raise RuntimeError("Accounting lane groups cannot restart")
        self._started = True
        try:
            # The first reporting lane owns the shared progress observation.
            await self.lanes[0].start(expires_at=expires_at)
            async with asyncio.TaskGroup() as starting:
                for lane in self.lanes[1:]:
                    starting.create_task(
                        lane.start(expires_at=expires_at), name="accounting-lane-start"
                    )
            if len(self.tasks) != len(self.lanes):
                raise RuntimeError("Every accounting lane requires its owned task")
        except BaseException:
            await self.close(expires_at=expires_at)
            raise

    async def close(self, *, expires_at: float) -> bool:
        self._closed = True
        self.stop_claims()
        results = await asyncio.gather(
            *(lane.close(expires_at=expires_at) for lane in self.lanes),
            return_exceptions=True,
        )
        return all(result is True for result in results)
