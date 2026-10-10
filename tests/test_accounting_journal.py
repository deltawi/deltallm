"""Journal acceptance freezes complete facts and never invents an event receipt."""

import asyncio
import hashlib
import json
from unittest.mock import AsyncMock

import pytest

from src.billing.accounting_journal import JournalReceipt, journal_batch
from src.billing.accounting_protocol import FinalizationReceipt
from src.db.accounting_journal import AccountingJournalRepository
from src.db.accounting_calls import AccountingProtocolUnavailable
from tests.test_accounting_local_leases import terminal


def deadline():
    return asyncio.get_running_loop().time() + 1


def rows(values):
    return [
        {
            "operation_id": str(value.finalization.operation_id),
            "journal_sequence": index + 1,
            "outcome": value.finalization.outcome.value,
            "replayed": False,
        }
        for index, value in enumerate(values)
    ]


async def test_snapshot_has_exact_hashes_and_no_clock_anchors():
    value = terminal()
    batch = journal_batch([value])
    identity = json.loads(batch.compact)[0]
    assert (
        identity["reservation_sha256"] == hashlib.sha256(batch.reservations[0].encode()).hexdigest()
    )
    assert (
        identity["finalization_sha256"]
        == hashlib.sha256(batch.finalizations[0].encode()).hexdigest()
    )
    assert "observed_monotonic" not in batch.compact
    assert identity["allowance_exact"] == "1.250000000000000000"
    value.finalization.audit_envelope["later"] = "changed"
    assert "later" not in batch.finalizations[0]


async def test_one_bulk_call_returns_only_typed_journal_receipts():
    values = [terminal() for _ in range(8)]
    db = AsyncMock()
    db.query_raw.return_value = list(reversed(rows(values)))
    result = await AccountingJournalRepository(db).append_batch(values, expires_at=deadline())
    assert db.query_raw.await_count == 1
    assert [receipt.operation_id for receipt in result] == [
        v.finalization.operation_id for v in values
    ]
    assert all(
        isinstance(receipt, JournalReceipt) and not isinstance(receipt, FinalizationReceipt)
        for receipt in result
    )
    assert all(not hasattr(receipt, "event_sequence") for receipt in result)


@pytest.mark.parametrize("failure", ["operation", "length", "sequence", "boolean", "outcome"])
async def test_invalid_acknowledgement_fails_without_retry(failure):
    values = [terminal(), terminal()]
    reply = rows(values)
    if failure == "operation":
        reply[1]["operation_id"] = reply[0]["operation_id"]
    elif failure == "length":
        reply.pop()
    elif failure == "sequence":
        reply[1]["journal_sequence"] = True
    elif failure == "boolean":
        reply[1]["replayed"] = 1
    else:
        reply[1]["outcome"] = "uncertain"
    db = AsyncMock()
    db.query_raw.return_value = reply
    with pytest.raises(AccountingProtocolUnavailable):
        await AccountingJournalRepository(db).append_batch(values, expires_at=deadline())
    assert db.query_raw.await_count == 1


@pytest.mark.parametrize("failure", ["duplicate", "nan", "entries", "generation"])
async def test_invalid_batch_never_calls_a_dependency(failure):
    values = [terminal()]
    if failure == "duplicate":
        values *= 2
    elif failure == "entries":
        values *= 257
    elif failure == "generation":
        values.append(
            terminal().model_copy(
                update={
                    "finalization": values[0].finalization.model_copy(
                        update={"protocol_generation": 8}
                    )
                }
            )
        )
    else:
        values[0].finalization.audit_envelope["bad"] = float("nan")
    db = AsyncMock()
    with pytest.raises(ValueError):
        await AccountingJournalRepository(db).append_batch(values, expires_at=deadline())
    db.query_raw.assert_not_awaited()


async def test_empty_batch_does_not_require_generation_or_database():
    db = AsyncMock()
    assert await AccountingJournalRepository(db).append_batch([], expires_at=deadline()) == ()
    db.query_raw.assert_not_awaited()


async def test_cancellation_keeps_the_caller_deadline_and_ends_the_write():
    entered = asyncio.Event()
    requested = deadline()

    async def blocked(*parameters):
        entered.set()
        await asyncio.Event().wait()

    db = AsyncMock()
    db.query_raw.side_effect = blocked
    task = asyncio.create_task(
        AccountingJournalRepository(db).append_batch([terminal()], expires_at=requested)
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert db.query_raw.await_count == 1
