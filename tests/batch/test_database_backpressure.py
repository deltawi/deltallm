from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.batch import worker as worker_module
from src.batch.retry import jittered_busy_wait
from src.batch.worker import BatchExecutorWorker
from src.batch.worker_persistence import WorkerPersistenceMixin
from src.batch.worker_types import BatchWorkerConfig
from src.bootstrap.batch_runtime.pool_sizing import require_batch_write_budget
from src.config import GeneralSettings
from src.database_caller import caller_priority, current_database_caller, database_caller

pytestmark = pytest.mark.hermetic


class _Allocation:
    def __init__(self, deep: list[bool]):
        self.deep = deep

    def queue_is_deep(self) -> bool:
        return self.deep.pop(0) if self.deep else False


def _worker(allocation: object | None) -> BatchExecutorWorker:
    state = SimpleNamespace(prisma_manager=SimpleNamespace(allocation=allocation))
    return BatchExecutorWorker(
        app=SimpleNamespace(state=state),
        repository=SimpleNamespace(),  # type: ignore[arg-type]
        storage=SimpleNamespace(),  # type: ignore[arg-type]
        config=BatchWorkerConfig(
            worker_id="w1", poll_interval_seconds=0.02, scheduler_mode="fifo_v1"
        ),
    )


async def test_worker_claims_nothing_while_claimed_work_waits_for_the_database(monkeypatch):
    worker = _worker(_Allocation([True, True, False]))
    claims: list[str] = []
    busy: list[str] = []
    monkeypatch.setattr(
        worker_module,
        "increment_batch_work_claim",
        lambda *, result, claim_mode: busy.append(result),
    )

    async def process_once() -> bool:
        claims.append("claim")
        worker.stop()
        return True

    monkeypatch.setattr(worker, "process_once", process_once)
    await asyncio.wait_for(worker.run(), timeout=2)

    assert busy == ["database_busy", "database_busy"]
    assert claims == ["claim"]


@pytest.mark.parametrize(
    "allocation",
    [None, SimpleNamespace(), SimpleNamespace(queue_is_deep=lambda: "yes")],
)
def test_worker_without_a_prioritized_pool_never_waits_for_it(allocation):
    assert _worker(allocation)._database_queue_is_deep() is False


def test_busy_waits_are_random_between_half_and_all_of_the_delay():
    waits = [jittered_busy_wait(2.0) for _ in range(500)]
    assert all(1.0 <= wait <= 2.0 for wait in waits)
    assert max(waits) - min(waits) > 0.5


def _general(**overrides) -> GeneralSettings:
    return GeneralSettings(
        **{
            "embeddings_batch_worker_enabled": True,
            "db_pool_size": 20,
            **overrides,
        }
    )


_NO_OVERRIDE = SimpleNamespace(db_pool_size=None)


@pytest.mark.parametrize(
    ("overrides", "settings", "checkpoint_writes"),
    [
        ({}, _NO_OVERRIDE, True),
        (
            {
                "embeddings_batch_accounting_write_concurrency": 20,
                "embeddings_batch_write_grouping_enabled": False,
            },
            _NO_OVERRIDE,
            True,
        ),
        (
            {
                "embeddings_batch_accounting_write_concurrency": 10,
                "embeddings_batch_write_grouping_enabled": True,
            },
            _NO_OVERRIDE,
            True,
        ),
        (
            {
                "embeddings_batch_accounting_write_concurrency": 30,
                "embeddings_batch_worker_enabled": False,
            },
            _NO_OVERRIDE,
            True,
        ),
        (
            {
                "embeddings_batch_accounting_write_concurrency": 15,
                "embeddings_batch_write_grouping_enabled": True,
            },
            SimpleNamespace(db_pool_size=30),
            True,
        ),
        # Without accounting v2 no checkpoint writer runs: only result saves
        # use the share.
        (
            {
                "embeddings_batch_accounting_write_concurrency": 15,
                "embeddings_batch_write_grouping_enabled": True,
            },
            _NO_OVERRIDE,
            False,
        ),
        # With neither writer running, no share is used.
        (
            {
                "embeddings_batch_accounting_write_concurrency": 30,
                "embeddings_batch_write_grouping_enabled": False,
            },
            _NO_OVERRIDE,
            False,
        ),
    ],
)
def test_write_budget_accepts_shares_that_fit_the_pool(overrides, settings, checkpoint_writes):
    require_batch_write_budget(_general(**overrides), settings, checkpoint_writes=checkpoint_writes)


@pytest.mark.parametrize(
    ("overrides", "settings", "checkpoint_writes", "message"),
    [
        (
            {
                "embeddings_batch_accounting_write_concurrency": 21,
                "embeddings_batch_write_grouping_enabled": False,
            },
            _NO_OVERRIDE,
            True,
            "gives billing checkpoints 21 slots, more than db_pool_size 20. Set it to at most 20",
        ),
        (
            {
                "embeddings_batch_accounting_write_concurrency": 11,
                "embeddings_batch_write_grouping_enabled": True,
            },
            _NO_OVERRIDE,
            True,
            "checkpoints and result saves 22 slots, more than db_pool_size 20. "
            "Set it to at most 10",
        ),
        (
            {
                "embeddings_batch_accounting_write_concurrency": 15,
                "embeddings_batch_write_grouping_enabled": True,
            },
            SimpleNamespace(db_pool_size=28),
            True,
            "30 slots, more than db_pool_size 28. Set it to at most 14",
        ),
        (
            {
                "embeddings_batch_accounting_write_concurrency": 21,
                "embeddings_batch_write_grouping_enabled": True,
            },
            _NO_OVERRIDE,
            False,
            "gives result saves 21 slots, more than db_pool_size 20. Set it to at most 20",
        ),
    ],
)
def test_write_budget_refuses_shares_past_the_pool(overrides, settings, checkpoint_writes, message):
    with pytest.raises(ValueError, match=message):
        require_batch_write_budget(
            _general(**overrides), settings, checkpoint_writes=checkpoint_writes
        )


async def test_ownership_read_after_a_skipped_save_waits_with_the_result_saves():
    # After a save that skipped a locked row, this read decides if the paid
    # answer is saved again or dropped. A claim must not displace it.
    callers: list[str] = []

    async def list_items_by_ids(item_ids):
        callers.append(current_database_caller())
        return [
            SimpleNamespace(item_id=item_id, status="in_progress", locked_by="w1", claim_epoch=3)
            for item_id in item_ids
        ]

    engine = SimpleNamespace(
        repository=SimpleNamespace(list_items_by_ids=list_items_by_ids),
        config=SimpleNamespace(worker_id="w1"),
    )
    with database_caller("batch_claim"):
        assert await WorkerPersistenceMixin._items_still_claimed(
            engine,  # type: ignore[arg-type]
            [{"item_id": "i1", "claim_epoch": 3}],
        )

    assert callers == ["batch_result_save"]
    assert caller_priority(callers[0]) < caller_priority("batch_claim")
