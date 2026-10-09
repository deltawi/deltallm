"""A bulk terminal keeps all proofs until the complete reply is valid."""

import asyncio
from uuid import uuid4

import pytest

from src.billing.accounting.permits.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting.journal.accounting_local_terminal import (
    LocalTerminalOwner,
    freeze_local_terminals,
    validated_terminal_acks,
)
from src.billing.accounting.accounting_protocol import AccountingOutcome
from src.billing.accounting.durable_microbatch import DurableBatchFull
from tests.test_accounting_local_issue import deadline
from tests.test_accounting_local_leases import terminal
from tests.test_accounting_local_receipts import acknowledgement

pytestmark = pytest.mark.asyncio


class Persistence:
    def __init__(self):
        self.calls = []
        self.entered = asyncio.Event()
        self.resume = None
        self.change = None

    async def finalize_batch(self, values, *, expires_at):
        self.calls.append(tuple(values))
        self.entered.set()
        if self.resume is not None:
            await self.resume.wait()
        results = [acknowledgement(value.receipt) for value in values]
        return results if self.change is None else self.change(results)


def state(count=2):
    values = [terminal() for _ in range(count)]
    receipts = LocalReceiptStore(max_entries=1024, max_retained_bytes=8 * 1024 * 1024)
    for value in values:
        assert receipts.retain(value.receipt)
    persistence = Persistence()
    owner = LocalTerminalOwner(persistence, receipts, generation=7)
    return values, receipts, persistence, owner


async def test_snapshot_keeps_request_and_terminal_payloads():
    value = terminal()
    value.receipt.reservation.pricing_snapshot["nested"] = {"rate": "before"}
    value.finalization.spend_payload["nested"] = {"amount": "before"}
    frozen = freeze_local_terminals([value], generation=7)
    value.receipt.reservation.pricing_snapshot["nested"]["rate"] = "after"
    value.finalization.spend_payload["nested"]["amount"] = "after"
    assert frozen[0].receipt.reservation.pricing_snapshot["nested"] == {"rate": "before"}
    assert frozen[0].finalization.spend_payload["nested"] == {"amount": "before"}


@pytest.mark.parametrize("failure", ["generation", "operation", "outcome", "length", "shape"])
async def test_bad_final_reply_is_rejected_as_one_complete_batch(failure):
    values = [terminal(), terminal()]
    results = [acknowledgement(value.receipt) for value in values]
    if failure == "generation":
        results[1] = results[1].model_copy(update={"protocol_generation": 8})
    elif failure == "operation":
        results[1] = results[1].model_copy(update={"operation_id": uuid4()})
    elif failure == "outcome":
        results[1] = results[1].model_copy(update={"outcome": AccountingOutcome.UNCERTAIN})
    elif failure == "length":
        results.pop()
    else:
        results[1] = {}
    with pytest.raises(RuntimeError):
        validated_terminal_acks(values, results)


@pytest.mark.parametrize("failure", ["duplicate", "stale", "entries", "bytes", "nan"])
async def test_invalid_input_is_rejected_before_a_dependency_call(failure):
    value = terminal()
    values = [value]
    if failure == "duplicate":
        values.append(value)
    elif failure == "stale":
        values = [
            value.model_copy(
                update={
                    "finalization": value.finalization.model_copy(update={"protocol_generation": 8})
                }
            )
        ]
    elif failure == "entries":
        values *= 257
    elif failure == "bytes":
        values = [terminal() for _ in range(8)]
        for item in values:
            item.finalization.spend_payload["large"] = "x" * 180_000
    else:
        value.finalization.audit_envelope["invalid"] = float("nan")
    with pytest.raises((ValueError, DurableBatchFull)):
        freeze_local_terminals(values, generation=7)


async def test_one_bulk_terminal_releases_exact_charges_and_replays_after_removal():
    values, receipts, persistence, owner = state(64)
    accepted = await owner.finalize_batch(values, expires_at=deadline())
    assert len(accepted) == 64 and len(persistence.calls) == 1
    assert receipts.entries == receipts.retained_bytes == 0
    persistence.change = lambda results: [
        result.model_copy(update={"replayed": True}) for result in results
    ]
    replay = await owner.finalize_batch(values, expires_at=deadline())
    assert all(result.replayed for result in replay)
    assert receipts.entries == receipts.retained_bytes == 0


async def test_invalid_late_reply_cannot_release_an_earlier_receipt():
    values, receipts, persistence, owner = state()
    charge = receipts.retained_bytes
    persistence.change = lambda results: [
        results[0],
        results[1].model_copy(update={"operation_id": uuid4()}),
    ]
    with pytest.raises(RuntimeError):
        await owner.finalize_batch(values, expires_at=deadline())
    assert receipts.entries == 2 and receipts.retained_bytes == charge


async def test_cancelled_write_retains_every_proof_and_charge():
    values, receipts, persistence, owner = state()
    charge = receipts.retained_bytes
    persistence.resume = asyncio.Event()
    task = asyncio.create_task(owner.finalize_batch(values, expires_at=deadline()))
    await persistence.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert receipts.entries == 2 and receipts.retained_bytes == charge


async def test_empty_terminal_batch_does_not_call_persistence():
    _, receipts, persistence, owner = state(0)
    assert await owner.finalize_batch([], expires_at=deadline()) == ()
    assert persistence.calls == [] and receipts.entries == 0


@pytest.mark.parametrize("expired", [0, float("nan"), float("inf")])
async def test_bad_deadline_cannot_write_a_terminal(expired):
    values, receipts, persistence, owner = state(1)
    with pytest.raises(RuntimeError):
        await owner.finalize_batch(values, expires_at=expired)
    assert persistence.calls == [] and receipts.entries == 1


@pytest.mark.parametrize("generation", [0, True, 2**63])
async def test_invalid_generation_cannot_construct_a_terminal_owner(generation):
    _, receipts, persistence, _ = state(0)
    with pytest.raises(ValueError):
        LocalTerminalOwner(persistence, receipts, generation=generation)


async def test_blocked_transport_cannot_exceed_the_callers_deadline():
    values, receipts, persistence, owner = state()
    charge = receipts.retained_bytes
    persistence.resume = asyncio.Event()
    with pytest.raises(TimeoutError):
        await owner.finalize_batch(values, expires_at=asyncio.get_running_loop().time() + 0.02)
    assert receipts.entries == 2 and receipts.retained_bytes == charge


@pytest.mark.parametrize("changed", [{"event_sequence": 0}, {"replayed": "true"}])
async def test_invalid_ack_scalar_does_not_release_a_proof(changed):
    values, receipts, persistence, owner = state(1)
    charge = receipts.retained_bytes
    persistence.change = lambda results: [results[0].model_copy(update=changed)]
    with pytest.raises(RuntimeError):
        await owner.finalize_batch(values, expires_at=deadline())
    assert receipts.entries == 1 and receipts.retained_bytes == charge


async def test_mutation_during_write_cannot_change_accepted_facts():
    values, receipts, persistence, owner = state(1)
    persistence.resume = asyncio.Event()
    values[0].finalization.spend_payload["nested"] = {"cost": "before"}
    task = asyncio.create_task(owner.finalize_batch(values, expires_at=deadline()))
    await persistence.entered.wait()
    values[0].finalization.spend_payload["nested"]["cost"] = "after"
    values[0].receipt.reservation.pricing_snapshot["version"] = "after"
    persistence.resume.set()
    await task
    assert persistence.calls[0][0].finalization.spend_payload["nested"] == {"cost": "before"}
    assert persistence.calls[0][0].receipt.reservation.pricing_snapshot["version"] == "price-v1"
    assert receipts.entries == receipts.retained_bytes == 0


@pytest.mark.parametrize("failure", ["identity", "duplicate"])
async def test_store_validates_the_whole_batch_before_removal(failure):
    values, receipts, _, _ = state()
    charge = receipts.retained_bytes
    proof = values[1].receipt
    proof = (
        proof.model_copy(update={"permit_ordinal": 1})
        if failure == "identity"
        else values[0].receipt
    )
    with pytest.raises(ValueError):
        receipts.acknowledge_batch(
            [
                (values[0].receipt, acknowledgement(values[0].receipt)),
                (proof, acknowledgement(proof)),
            ]
        )
    assert receipts.entries == 2 and receipts.retained_bytes == charge


async def test_expiry_after_ack_preparation_cannot_remove_any_proof(monkeypatch):
    from src.billing.accounting.journal import accounting_local_terminal
    from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
    from src.db.runtime.telemetry_acceptance import AcceptanceFailure

    values, receipts, persistence, owner = state()
    charge = receipts.retained_bytes
    checks = []

    def check_deadline(expires_at):
        checks.append(expires_at)
        if len(checks) == 2:
            raise AccountingProtocolUnavailable(AcceptanceFailure.DEADLINE)

    monkeypatch.setattr(accounting_local_terminal, "_caller_deadline", check_deadline)
    with pytest.raises(AccountingProtocolUnavailable):
        await owner.finalize_batch(values, expires_at=deadline())
    assert len(persistence.calls) == 1 and len(checks) == 2
    assert receipts.entries == 2 and receipts.retained_bytes == charge
