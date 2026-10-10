"""Worker handles preserve deadlines and reject partial or invalid results."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.billing.accounting.journal.accounting_journal_claims import JournalClaim, JournalFailure
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting.journal.accounting_journal_worker import AccountingJournalWorkerRepository


def deadline():
    return asyncio.get_running_loop().time() + 1


def handle():
    return JournalClaim(
        protocol_generation=7, worker_id="worker", lease_token=uuid4(), sequences=(1, 2)
    )


async def test_claim_and_materialization_are_one_call_each():
    db = AsyncMock()
    db.query_raw.side_effect = [[{"journal_sequence": 1}, {"journal_sequence": 2}], [{"count": 2}]]
    repo = AccountingJournalWorkerRepository(db)
    claim = await repo.claim(generation=7, worker_id="worker", expires_at=deadline())
    assert claim.sequences == (1, 2)
    assert await repo.materialize(claim, expires_at=deadline()) == 2
    assert db.query_raw.await_count == 2


async def test_lost_claim_reply_recovers_only_the_original_nonce():
    db = AsyncMock()
    db.query_raw.side_effect = [TimeoutError(), [{"journal_sequence": 1}]]
    claim = await AccountingJournalWorkerRepository(db).claim(
        generation=7, worker_id="worker", expires_at=deadline()
    )
    calls = db.query_raw.call_args_list
    assert calls[0].args[3] == calls[1].args[3] == str(claim.lease_token)
    assert claim.sequences == (1,) and len(calls) == 2


async def test_lost_commit_reply_uses_exact_completed_keys_not_another_claim():
    db = AsyncMock()
    db.query_raw.side_effect = [TimeoutError(), [{"count": 2}]]
    claim = handle()
    assert (
        await AccountingJournalWorkerRepository(db).materialize(claim, expires_at=deadline()) == 2
    )
    assert "recover" not in db.query_raw.call_args_list[0].args[0]
    assert db.query_raw.call_args_list[1].args[1] == [1, 2]


@pytest.mark.parametrize(
    "reply", [[], [{"count": True}], [{"count": -1}], [{"count": 3}], [{"count": "2"}]]
)
async def test_invalid_commit_result_fails_closed(reply):
    db = AsyncMock()
    db.query_raw.return_value = reply
    with pytest.raises(AccountingProtocolUnavailable):
        await AccountingJournalWorkerRepository(db).materialize(handle(), expires_at=deadline())
    assert db.query_raw.await_count == 1


@pytest.mark.parametrize(
    "reply",
    [[{"journal_sequence": True}], [{"journal_sequence": 0}], [{"journal_sequence": 1}] * 2],
)
async def test_invalid_claim_result_fails_closed(reply):
    db = AsyncMock()
    db.query_raw.return_value = reply
    with pytest.raises(AccountingProtocolUnavailable):
        await AccountingJournalWorkerRepository(db).claim(
            generation=7, worker_id="worker", expires_at=deadline()
        )
    assert db.query_raw.await_count == 1


@pytest.mark.parametrize("value", [True, -1, 0, 2**63, "1"])
async def test_model_copy_cannot_bypass_sequence_validation(value):
    claim = handle().model_copy(update={"sequences": (value,)})
    db = AsyncMock()
    with pytest.raises(ValueError):
        await AccountingJournalWorkerRepository(db).materialize(claim, expires_at=deadline())
    db.query_raw.assert_not_awaited()


async def test_empty_claim_does_not_write_or_change_failure_state():
    claim = handle().model_copy(update={"sequences": ()})
    db = AsyncMock()
    repo = AccountingJournalWorkerRepository(db)
    assert await repo.materialize(claim, expires_at=deadline()) == 0
    assert await repo.fail(claim, JournalFailure.PERSISTENCE, expires_at=deadline()) == 0
    db.query_raw.assert_not_awaited()


async def test_cancelled_commit_does_not_claim_again():
    db = AsyncMock()
    db.query_raw.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await AccountingJournalWorkerRepository(db).materialize(handle(), expires_at=deadline())
    assert db.query_raw.await_count == 1
