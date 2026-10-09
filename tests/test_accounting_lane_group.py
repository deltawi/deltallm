"""Bounded lane groups retain existing fences, task ownership and pool reserve."""

import asyncio
from collections.abc import Mapping
from unittest.mock import AsyncMock

import pytest

from src.billing.accounting.accounting_lane_group import AccountingLaneGroup
from src.bootstrap.accounting_role_builders import native_processing_lanes
from src.telemetry.lifecycle import WorkerHealth, WorkerState, stop_tasks_before_deadline
from tests.test_accounting_native_config import native


def deadline() -> float:
    return asyncio.get_running_loop().time() + 1


class Lane:
    def __init__(self) -> None:
        self.task: asyncio.Task[None] | None = None
        self.worker_health = WorkerHealth(WorkerState.STARTING)
        self.retained_claim_bytes = 16_384
        self.stopped = asyncio.Event()
        self.start_error = False
        self.close_error = False
        self.started_deadline: float | None = None
        self.closed_deadline: float | None = None

    async def start(self, *, expires_at: float) -> None:
        self.started_deadline = expires_at
        if self.start_error:
            self.worker_health = WorkerHealth(WorkerState.FAILED)
            raise RuntimeError("Synthetic lane startup failure")
        self.task = asyncio.create_task(self.stopped.wait(), name="test-owned-lane")
        self.worker_health = WorkerHealth(WorkerState.READY)

    def stop_claims(self) -> None:
        self.stopped.set()

    async def close(self, *, expires_at: float) -> bool:
        self.closed_deadline = expires_at
        self.stop_claims()
        clean = await stop_tasks_before_deadline((self.task,), deadline=expires_at)
        self.worker_health = WorkerHealth(WorkerState.DISABLED)
        if self.close_error:
            raise RuntimeError("Synthetic lane close failure")
        return clean


@pytest.mark.parametrize("count", [1, 2, 4])
async def test_groups_own_every_task_and_one_shared_close_deadline(count: int) -> None:
    lanes = tuple(Lane() for _ in range(count))
    group = AccountingLaneGroup(lanes)
    expires = deadline()
    await group.start(expires_at=expires)
    assert group.worker_health.state is WorkerState.READY
    assert len(group.tasks) == count
    assert group.retained_claim_bytes == count * 16_384
    assert all(lane.started_deadline == expires for lane in lanes)
    group.stop_claims()
    assert await group.close(expires_at=expires)
    assert all(task.done() for task in group.tasks)
    assert all(lane.closed_deadline == expires for lane in lanes)
    with pytest.raises(RuntimeError, match="cannot restart"):
        await group.start(expires_at=deadline())


@pytest.mark.parametrize("state", [WorkerState.FAILED, WorkerState.DEGRADED, WorkerState.STOPPING])
async def test_one_unready_lane_withdraws_group_health(state: WorkerState) -> None:
    lanes = (Lane(), Lane())
    group = AccountingLaneGroup(lanes)
    await group.start(expires_at=deadline())
    try:
        lanes[1].worker_health = WorkerHealth(state, "bounded_failure")
        assert group.worker_health == WorkerHealth(state, "bounded_failure")
    finally:
        assert await group.close(expires_at=deadline())


async def test_startup_failure_stops_every_started_lane() -> None:
    lanes = tuple(Lane() for _ in range(4))
    lanes[-1].start_error = True
    group = AccountingLaneGroup(lanes)
    with pytest.raises(ExceptionGroup):
        await group.start(expires_at=deadline())
    assert all(task.done() for task in group.tasks)
    assert all(lane.stopped.is_set() for lane in lanes)
    assert group.worker_health.state is not WorkerState.READY


async def test_close_failure_is_not_reported_as_a_clean_shutdown() -> None:
    lanes = (Lane(), Lane())
    group = AccountingLaneGroup(lanes)
    await group.start(expires_at=deadline())
    lanes[1].close_error = True
    assert not await group.close(expires_at=deadline())
    assert all(task.done() for task in group.tasks)


@pytest.mark.parametrize("count", [0, 5])
def test_group_rejects_unbounded_or_empty_lane_inventory(count: int) -> None:
    with pytest.raises(ValueError):
        AccountingLaneGroup(tuple(Lane() for _ in range(count)))


def test_group_cannot_register_the_same_lane_twice() -> None:
    lane = Lane()
    with pytest.raises(ValueError):
        AccountingLaneGroup((lane, lane))


class NoDatabase:
    async def query_raw(self, query: str, *parameters: object) -> list[Mapping[str, object]]:
        raise AssertionError("Construction must not open or query the database")


def test_native_builders_use_two_terminal_four_reporting_lanes_and_one_progress_owner() -> None:
    config = native(accounting_projection_worker_enabled=True, accounting_projection_batch_size=256)
    terminals, reports, health = native_processing_lanes(NoDatabase(), config, owner_id="worker")
    assert len(terminals.lanes) == 2 and len(reports.lanes) == 4
    assert config.accounting_hot_path_db_pool_size - 6 == 2
    assert len({lane._config.worker_id for lane in (*terminals.lanes, *reports.lanes)}) == 6
    assert all(lane._config.batch_size == 256 for lane in (*terminals.lanes, *reports.lanes))
    assert all(lane.progress_health is health for lane in reports.lanes)
    assert sum(lane._observe_progress for lane in reports.lanes) == 1


async def test_secondary_reporting_lane_does_not_duplicate_progress_queries() -> None:
    config = native(accounting_projection_worker_enabled=True)
    _, reports, _ = native_processing_lanes(NoDatabase(), config, owner_id="worker")
    secondary = reports.lanes[1]
    secondary._persistence.progress = AsyncMock(
        side_effect=AssertionError("Duplicate progress query")
    )
    await secondary._refresh_progress(expires_at=deadline())
    secondary._persistence.progress.assert_not_awaited()
