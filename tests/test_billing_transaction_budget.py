"""Billing transactions keep the shared 250 ms bound unless a caller passes its own."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from src.billing.spend.spend_operations import ADMISSION_BUDGET_SECONDS
from src.db.billing.billing_transaction import DB_BUDGET_SECONDS, billing_transaction


class RecordingDB:
    def __init__(self) -> None:
        self.tx_kwargs: dict = {}
        self.timeouts: list[str] = []

    @asynccontextmanager
    async def tx(self, **kwargs):
        self.tx_kwargs = kwargs
        yield self

    async def query_raw(self, _sql, timeout):
        self.timeouts.append(timeout)


def _far_deadline() -> float:
    return asyncio.get_running_loop().time() + 30


async def test_default_budget_stays_at_the_shared_bound():
    db = RecordingDB()
    async with billing_transaction(db, _far_deadline()):
        pass
    assert db.tx_kwargs["timeout"].total_seconds() == pytest.approx(DB_BUDGET_SECONDS, abs=0.01)
    assert db.timeouts == [f"{int(DB_BUDGET_SECONDS * 1000)}ms"]


async def test_admission_budget_widens_only_the_caller_that_asks():
    db = RecordingDB()
    async with billing_transaction(db, _far_deadline(), budget=ADMISSION_BUDGET_SECONDS):
        pass
    assert db.tx_kwargs["timeout"].total_seconds() == pytest.approx(
        ADMISSION_BUDGET_SECONDS, abs=0.01
    )
    assert db.timeouts[0].endswith("ms")
    assert int(db.timeouts[0][:-2]) > int(DB_BUDGET_SECONDS * 1000)


async def test_caller_deadline_still_caps_a_wider_budget():
    db = RecordingDB()
    deadline = asyncio.get_running_loop().time() + 0.1
    async with billing_transaction(db, deadline, budget=ADMISSION_BUDGET_SECONDS):
        pass
    assert db.tx_kwargs["timeout"].total_seconds() <= 0.1
