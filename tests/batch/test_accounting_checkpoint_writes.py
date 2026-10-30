import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.batch.accounting_checkpoint import BatchAccountingUnavailable, BatchAccountingWrite
from src.batch.repositories.accounting_repository import (
    BatchAccountingRepository,
    WRITE_CHECKPOINTS_SQL,
)
from src.batch.selector_checkpoint import BatchSelectorClaim
from src.bootstrap.batch_runtime.accounting import batch_checkpoint_write_concurrency
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
