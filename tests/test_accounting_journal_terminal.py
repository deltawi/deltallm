"""Journal acknowledgements use the shared owner without fake event keys."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.accounting_adapters.journal_terminal import JournalTerminalPersistence
from src.billing.accounting.permits.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting.journal.accounting_local_terminal import LocalTerminalOwner
from src.billing.accounting.accounting_protocol import AccountingOutcome
from src.billing.accounting.journal.accounting_terminal_receipts import JournalReceipt
from tests.test_accounting_local_issue import deadline
from tests.test_accounting_local_leases import terminal
from tests.test_accounting_local_receipts import acknowledgement


def state(count=2):
    values = [terminal() for _ in range(count)]
    receipts = LocalReceiptStore(max_entries=1024, max_retained_bytes=8 * 1024 * 1024)
    for value in values:
        assert receipts.retain(value.receipt)
    results = [
        JournalReceipt(
            protocol_generation=7,
            operation_id=value.finalization.operation_id,
            journal_sequence=index + 1,
            outcome=value.finalization.outcome,
        )
        for index, value in enumerate(values)
    ]
    journal = AsyncMock()
    journal.append_batch.return_value = results
    owner = LocalTerminalOwner(
        JournalTerminalPersistence(journal), receipts, generation=7, receipt_type=JournalReceipt
    )
    return values, receipts, journal, owner


async def test_journal_receipt_releases_local_proofs_but_has_no_canonical_event():
    values, receipts, journal, owner = state(64)
    accepted = await owner.finalize_batch(values, expires_at=deadline())
    assert all(type(ack) is JournalReceipt for ack in accepted)
    assert all("event_sequence" not in ack.model_dump() for ack in accepted)
    assert receipts.entries == receipts.retained_bytes == 0
    assert journal.append_batch.await_count == 1
    journal.append_batch.return_value = [
        ack.model_copy(update={"replayed": True}) for ack in accepted
    ]
    replay = await owner.finalize_batch(values, expires_at=deadline())
    assert [ack.journal_sequence for ack in replay] == [ack.journal_sequence for ack in accepted]
    assert all(ack.replayed for ack in replay)
    assert receipts.entries == receipts.retained_bytes == 0


@pytest.mark.parametrize(
    "failure",
    [
        "kind",
        "length",
        "shape",
        "operation",
        "generation",
        "outcome",
        "sequence",
        "boolean_sequence",
        "overflow_sequence",
        "replayed",
    ],
)
async def test_invalid_complete_ack_batch_keeps_every_local_proof(failure):
    values, receipts, journal, owner = state()
    charged = receipts.retained_bytes
    results = list(journal.append_batch.return_value)
    if failure == "kind":
        results[1] = acknowledgement(values[1].receipt)
    elif failure == "length":
        results.pop()
    elif failure == "shape":
        results[1] = {}
    else:
        changed = {
            "operation": {"operation_id": uuid4()},
            "generation": {"protocol_generation": 8},
            "outcome": {"outcome": AccountingOutcome.UNCERTAIN},
            "sequence": {"journal_sequence": 0},
            "boolean_sequence": {"journal_sequence": True},
            "overflow_sequence": {"journal_sequence": 2**63},
            "replayed": {"replayed": "true"},
        }
        results[1] = results[1].model_copy(update=changed[failure])
    journal.append_batch.return_value = results
    with pytest.raises(RuntimeError):
        await owner.finalize_batch(values, expires_at=deadline())
    assert receipts.entries == 2 and receipts.retained_bytes == charged


async def test_canonical_owner_rejects_a_journal_acknowledgement():
    values, receipts, journal, _ = state(1)
    canonical = LocalTerminalOwner(JournalTerminalPersistence(journal), receipts, generation=7)
    with pytest.raises(RuntimeError):
        await canonical.finalize_batch(values, expires_at=deadline())
    assert receipts.entries == 1


async def test_cancellation_keeps_local_proofs_until_exact_retry():
    values, receipts, journal, owner = state()
    charged = receipts.retained_bytes
    journal.append_batch.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await owner.finalize_batch(values, expires_at=deadline())
    assert receipts.entries == 2 and receipts.retained_bytes == charged
    journal.append_batch.side_effect = None
    assert len(await owner.finalize_batch(values, expires_at=deadline())) == 2
    assert receipts.entries == receipts.retained_bytes == 0


@pytest.mark.parametrize("receipt_type", [dict, None, bool])
async def test_unknown_receipt_contract_cannot_construct_an_owner(receipt_type):
    _, receipts, journal, _ = state(0)
    with pytest.raises(ValueError):
        LocalTerminalOwner(
            JournalTerminalPersistence(journal), receipts, generation=7, receipt_type=receipt_type
        )


@pytest.mark.parametrize("path", ["provider", "paid_cache"])
async def test_provider_and_paid_cache_keep_the_same_journal_acceptance_owner(path):
    from src.billing.accounting.accounting_local_service import LocalAccountingService
    from src.billing.charges.provider_allowance import ProviderRequestBounds
    from src.cache.middleware import CacheMiddleware
    from src.telemetry.spend_operation import durable_provider_call, operation_handle
    from tests.test_accounting_local_service import state as service_state, ingestion
    from tests.test_accounting_request_path import (
        _AccountingRepository,
        _deployment,
        _priced_cache_entry,
        _request,
    )

    _, _, _, receipts, issuer, _ = service_state()
    journal = AsyncMock()

    async def accept(values, *, expires_at):
        return [
            JournalReceipt(
                protocol_generation=7,
                operation_id=value.finalization.operation_id,
                journal_sequence=index + 1,
                outcome=value.finalization.outcome,
            )
            for index, value in enumerate(values)
        ]

    journal.append_batch.side_effect = accept
    owner = LocalTerminalOwner(
        JournalTerminalPersistence(journal),
        receipts,
        generation=7,
        receipt_type=JournalReceipt,
    )
    service = LocalAccountingService(
        _AccountingRepository(),
        generation=7,
        issuer=issuer,
        terminal=owner,
        dwell_seconds=0,
    )
    spend = ingestion(service)
    request = _request(service, spend)
    service.start()
    try:
        if path == "provider":
            await durable_provider_call(
                request,
                model="gpt-test",
                call_type="completion",
                deployment=_deployment(),
                bounds=ProviderRequestBounds(max_output_tokens=10),
                execute=AsyncMock(return_value="ok"),
            )
            operation = operation_handle(request)
            await spend._finalize_accounting_spend(
                operation,
                event_id=str(operation.reservation.operation_id),
                payload={"cost_exact": "0.3"},
            )
        else:
            await CacheMiddleware(AsyncMock())._record_cache_hit_accounting(
                request,
                "/v1/chat/completions",
                "gpt-test",
                "cache:test",
                _priced_cache_entry(),
            )
            operation = operation_handle(request)
        written = journal.append_batch.call_args.args[0][0]
        assert written.receipt == operation.proof
        assert journal.append_batch.await_count == 1
        assert receipts.entries == receipts.retained_bytes == 0
        spend.writer.log_spend.assert_not_called()
    finally:
        await service.close()
