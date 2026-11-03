import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.batch.accounting_checkpoint import (
    CHECKPOINT_BATCH_MAX_ITEMS,
    BatchAccountingUnavailable,
    BatchAccountingWrite,
)
from src.batch.repositories.accounting_repository import (
    BatchAccountingRepository,
    WRITE_CHECKPOINTS_SQL,
)
from src.batch.selector_checkpoint import BatchSelectorClaim
from src.bootstrap.batch_runtime.accounting import batch_checkpoint_write_concurrency
from src.bootstrap.batch_runtime.pool_sizing import batch_result_save_concurrency
from src.config import GeneralSettings
from src.metrics.batch import (
    deltallm_batch_accounting_checkpoint_write_metric,
    deltallm_batch_accounting_checkpoint_write_retries_metric,
)
from tests.batch.accounting_fixtures import checkpoint_for


class FakeDb:
    """Answer checkpoint SQL with scripted outcomes and track open transactions."""

    def __init__(self, outcomes, *, hold=0.0):
        self.outcomes = list(outcomes)
        self.hold = hold
        self.calls = 0
        self.open = 0
        self.peak = 0

    @asynccontextmanager
    async def tx(self, *, max_wait, timeout):
        assert 0 < timeout.total_seconds() <= max_wait.total_seconds() + 1e-9
        self.open += 1
        self.peak = max(self.peak, self.open)
        try:
            yield self
        finally:
            self.open -= 1

    async def query_raw(self, sql, *args):
        if sql is not WRITE_CHECKPOINTS_SQL:
            return []
        self.calls += 1
        await asyncio.sleep(self.hold)
        outcome = self.outcomes.pop(0) if self.outcomes else "saved"
        if outcome == "timeout":
            raise TimeoutError()
        if outcome == "rejected":
            return []
        return [{"item_id": "item", "delivery_saved": True}]


def _write(item_id="item"):
    claim = BatchSelectorClaim("batch", item_id, "key", "worker", 1)
    return BatchAccountingWrite(claim, None, checkpoint_for(claim))


def _deadline(seconds):
    return asyncio.get_running_loop().time() + seconds


def _sample(metric, **labels):
    for family in metric.collect():
        for sample in family.samples:
            if sample.name.endswith(("_count", "_total")) and sample.labels == labels:
                return sample.value
    return 0.0


async def test_timeout_is_retried_once_within_the_budget():
    db = FakeDb(["timeout", "saved"])
    retries = _sample(deltallm_batch_accounting_checkpoint_write_retries_metric)
    await BatchAccountingRepository(db).write_many([_write()], expires_at=_deadline(2))
    assert db.calls == 2
    assert _sample(deltallm_batch_accounting_checkpoint_write_retries_metric) == retries + 1


async def test_rejected_write_is_not_retried():
    db = FakeDb(["rejected", "saved"])
    rejected = _sample(deltallm_batch_accounting_checkpoint_write_metric, outcome="rejected")
    with pytest.raises(BatchAccountingUnavailable):
        await BatchAccountingRepository(db).write_many([_write()], expires_at=_deadline(2))
    assert db.calls == 1
    assert (
        _sample(deltallm_batch_accounting_checkpoint_write_metric, outcome="rejected")
        == rejected + 1
    )


async def test_short_budget_gets_one_attempt():
    db = FakeDb(["timeout", "saved"])
    with pytest.raises(BatchAccountingUnavailable):
        await BatchAccountingRepository(db).write_many([_write()], expires_at=_deadline(0.3))
    assert db.calls == 1


async def test_writes_beyond_the_limit_queue_instead_of_failing():
    db = FakeDb([], hold=0.05)
    store = BatchAccountingRepository(db, max_concurrent_writes=2)
    await asyncio.gather(
        *(
            store.write_many([_write(f"item-{index}")], expires_at=_deadline(2))
            for index in range(8)
        )
    )
    assert db.calls == 8
    assert db.peak == 2


def test_write_concurrency_defaults_to_half_the_pool():
    general = GeneralSettings(db_pool_size=20)
    assert batch_checkpoint_write_concurrency(general, SimpleNamespace(db_pool_size=None)) == 10
    assert batch_checkpoint_write_concurrency(general, SimpleNamespace(db_pool_size=6)) == 3
    explicit = GeneralSettings(db_pool_size=20, embeddings_batch_accounting_write_concurrency=4)
    assert batch_checkpoint_write_concurrency(explicit, SimpleNamespace(db_pool_size=None)) == 4


class GroupingDb(FakeDb):
    """Save every proof of a document, or none when it holds a rejected item."""

    def __init__(self, *, reject=(), hold=0.05):
        super().__init__([], hold=hold)
        self.reject = set(reject)
        self.documents: list[list[str]] = []

    async def query_raw(self, sql, *args):
        if sql is not WRITE_CHECKPOINTS_SQL:
            return []
        self.calls += 1
        item_ids = [entry["item_id"] for entry in json.loads(args[0])]
        self.documents.append(item_ids)
        await asyncio.sleep(self.hold)
        if self.reject & set(item_ids):
            return []
        return [{"item_id": item_id, "delivery_saved": True} for item_id in item_ids]


async def _write_all(store, names):
    return await asyncio.gather(
        *(store.write_many([_write(name)], expires_at=_deadline(2)) for name in names),
        return_exceptions=True,
    )


async def test_proofs_waiting_for_a_slot_are_saved_in_one_statement():
    db = GroupingDb()
    store = BatchAccountingRepository(db, max_concurrent_writes=1, group_waiting_writes=True)
    results = await _write_all(store, [f"item-{index}" for index in range(5)])
    assert results == [None] * 5
    assert db.documents == [["item-0"], ["item-1", "item-2", "item-3", "item-4"]]
    assert db.peak == 1


async def test_a_rejected_group_saves_every_other_proof_alone():
    db = GroupingDb(reject={"item-2"})
    store = BatchAccountingRepository(db, max_concurrent_writes=1, group_waiting_writes=True)
    rejected = _sample(deltallm_batch_accounting_checkpoint_write_metric, outcome="rejected")
    saved = _sample(deltallm_batch_accounting_checkpoint_write_metric, outcome="saved")
    results = await _write_all(store, ["item-0", "item-1", "item-2", "item-3"])
    assert results[:2] == [None, None] and results[3] is None
    assert isinstance(results[2], BatchAccountingUnavailable)
    assert db.documents == [
        ["item-0"],
        ["item-1", "item-2", "item-3"],
        ["item-1"],
        ["item-2"],
        ["item-3"],
    ]
    # Each caller is counted once, with its own answer.
    assert _sample(deltallm_batch_accounting_checkpoint_write_metric, outcome="rejected") == (
        rejected + 1
    )
    assert _sample(deltallm_batch_accounting_checkpoint_write_metric, outcome="saved") == saved + 3


async def test_two_writes_for_one_item_are_never_grouped():
    db = GroupingDb()
    store = BatchAccountingRepository(db, max_concurrent_writes=1, group_waiting_writes=True)
    await _write_all(store, ["first", "item", "other", "item"])
    assert db.documents == [["first"], ["item", "other"], ["item"]]


async def test_a_group_stays_within_the_proof_count_limit():
    db = GroupingDb(hold=0.01)
    store = BatchAccountingRepository(db, max_concurrent_writes=1, group_waiting_writes=True)
    names = [f"item-{index}" for index in range(CHECKPOINT_BATCH_MAX_ITEMS + 3)]
    assert await _write_all(store, names) == [None] * len(names)
    assert [len(document) for document in db.documents] == [1, CHECKPOINT_BATCH_MAX_ITEMS, 2]


async def test_grouping_off_keeps_one_statement_per_write():
    db = GroupingDb()
    store = BatchAccountingRepository(db, max_concurrent_writes=1)
    assert await _write_all(store, ["a", "b", "c"]) == [None] * 3
    assert db.documents == [["a"], ["b"], ["c"]]


def test_result_saves_get_as_many_slots_only_with_grouping_on():
    pool = SimpleNamespace(db_pool_size=None)
    assert batch_result_save_concurrency(GeneralSettings(db_pool_size=20), pool) == 10
    explicit = GeneralSettings(db_pool_size=20, embeddings_batch_accounting_write_concurrency=4)
    assert batch_result_save_concurrency(explicit, pool) == 4
    off = GeneralSettings(db_pool_size=20, embeddings_batch_write_grouping_enabled=False)
    assert batch_result_save_concurrency(off, pool) == 0
    assert batch_result_save_concurrency(SimpleNamespace(), pool) == 0


async def test_a_member_about_to_time_out_does_not_shorten_its_group():
    db = GroupingDb(hold=0.0)
    store = BatchAccountingRepository(db, max_concurrent_writes=1, group_waiting_writes=True)
    healthy = store._batch([_write("healthy")], expires_at=_deadline(2))
    late = store._batch([_write("late")], expires_at=_deadline(0.01))
    budgets: list[float] = []
    original_tx = db.tx

    def tx(*, max_wait, timeout):
        budgets.append(timeout.total_seconds())
        return original_tx(max_wait=max_wait, timeout=timeout)

    db.tx = tx
    assert await store._write_group([late, healthy]) is True
    assert db.documents == [["late", "healthy"]]
    # The healthy member's budget, split for one retry, not the late one's 10 ms.
    assert budgets and budgets[0] > 0.5
