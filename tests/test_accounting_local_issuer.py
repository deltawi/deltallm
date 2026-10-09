"""Bulk funding precedes local issue; failures consume no warm prefix."""

import asyncio

import pytest

from src.billing.accounting.permits.accounting_local_cursors import LocalCursorStore
from src.billing.accounting.permits.accounting_local_issuer import LocalPermitIssuer
from src.billing.accounting.permits.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting.accounting_protocol import ReserveDecision
from src.billing.accounting.durable_microbatch import DurableBatchClosed, DurableBatchFull
from src.billing.accounting.permits.preissued_permits import PermitSubject
from src.db.accounting_calls import AccountingProtocolUnavailable
from tests.test_accounting_local_issue import deadline
from tests.test_accounting_local_leases import grant, funding_row, terminal
from tests.test_preissued_permit_bank import fresh


class Funding:
    def __init__(self, *, maximum=1024, decision=None, fail_at=None):
        self.maximum = maximum
        self.decision = decision
        self.fail_at = fail_at
        self.calls = []
        self.entered = asyncio.Event()
        self.continue_funding = None
        self.pause_at = None
        self.paused = asyncio.Event()
        self.resume = asyncio.Event()

    async def allocate_batch(self, allocations, *, expires_at):
        self.calls.append(tuple(allocations))
        self.entered.set()
        if self.pause_at == len(self.calls):
            self.paused.set()
            await self.resume.wait()
        if self.continue_funding is not None:
            await self.continue_funding.wait()
        if self.fail_at == len(self.calls):
            raise RuntimeError("funding failed")
        if self.decision is not None:
            return [self.decision] * len(allocations)
        results = []
        for item in allocations:
            row = funding_row(item)
            row["operation_limit"] = min(item.target_operations, self.maximum)
            results.append(grant(item, row))
        return results


def state(persistence=None, *, cursor_entries=1024, receipt_entries=1024):
    persistence = persistence or Funding()
    cursors = LocalCursorStore(
        generation=7, max_entries=cursor_entries, max_retained_bytes=8 * 1024 * 1024
    )
    retained = LocalReceiptStore(max_entries=receipt_entries, max_retained_bytes=8 * 1024 * 1024)
    return (
        persistence,
        cursors,
        retained,
        LocalPermitIssuer(persistence, cursors, retained, target_operations=4),
    )


def items(count, original=None):
    original = original or terminal().receipt.reservation
    return [fresh(original) for _ in range(count)]


async def test_one_cold_round_then_zero_calls_for_warm_issue():
    persistence, cursors, retained, issuer = state()
    first = items(1)
    result = await issuer.reserve_batch(first, expires_at=deadline())
    assert len(persistence.calls) == 1 and len(result.proofs) == 1
    subject = PermitSubject.from_reservation(first[0])
    assert cursors.get(subject).next_ordinal == 1
    assert cursors.available_permits == 3
    result = await issuer.reserve_batch(items(3, first[0]), expires_at=deadline())
    assert len(persistence.calls) == 1 and len(result.proofs) == 3
    assert cursors.available_permits == cursors.entries == 0
    assert retained.entries == 4


async def test_two_partial_funding_rounds_keep_every_accepted_prefix():
    persistence, cursors, retained, issuer = state(Funding(maximum=2))
    result = await issuer.reserve_batch(items(6), expires_at=deadline())
    assert len(persistence.calls) == 2
    assert [permit.decision for permit in result.permits] == [ReserveDecision.DISPATCH] * 4 + [
        ReserveDecision.CAPACITY_EXHAUSTED
    ] * 2
    assert len(result.proofs) == retained.entries == 4
    assert cursors.staged_grants == cursors.available_permits == 0


async def test_many_subjects_are_funded_in_one_call_not_an_awaited_subject_loop():
    persistence, cursors, retained, issuer = state()
    original = items(1)[0]
    values = [
        fresh(original).model_copy(
            update={
                "attribution": original.attribution.model_copy(update={"model": f"subject-{index}"})
            }
        )
        for index in range(64)
    ]
    result = await issuer.reserve_batch(values, expires_at=deadline())
    assert len(persistence.calls) == 1 and len(persistence.calls[0]) == 64
    assert [permit.operation_id for permit in result.permits] == [
        item.operation_id for item in values
    ]
    assert len(result.proofs) == retained.entries == 64
    assert cursors.entries == cursors.available_permits == 0


@pytest.mark.parametrize("failure", ["error", "cancel", "closed"])
async def test_later_cold_failure_preserves_warm_prefix_and_returns_only_new_grants(failure):
    persistence = Funding(maximum=1, fail_at=2 if failure == "error" else None)
    persistence.pause_at = 2
    _, cursors, retained, issuer = state(persistence)
    proof = terminal().receipt
    subject = PermitSubject.from_reservation(proof.reservation)
    assert cursors.add(subject, proof.grant)
    # The warm grant serves four; two more requests require both cold rounds.
    task = asyncio.create_task(
        issuer.reserve_batch(items(6, proof.reservation), expires_at=deadline())
    )
    async with asyncio.timeout(1):
        await persistence.paused.wait()
    if failure == "cancel":
        task.cancel()
    elif failure == "closed":
        issuer.stop_admission()
    if failure != "cancel":
        persistence.resume.set()
    error = {"error": RuntimeError, "cancel": asyncio.CancelledError, "closed": DurableBatchClosed}[
        failure
    ]
    with pytest.raises(error):
        await task
    assert cursors.get(subject).next_ordinal == 0 and cursors.available_permits == 4
    assert retained.entries == retained.retained_bytes == 0
    assert cursors.staged_grants == 0 and cursors.retiring_grants >= 1
    assert all(item.first_unused_ordinal == 0 for item in cursors.return_candidates())
    assert not issuer.admission.active and not issuer.admission.waiters


async def test_duplicate_live_operation_replays_without_another_dispatch_or_sql_call():
    persistence, cursors, retained, issuer = state()
    value = items(1)[0]
    await issuer.reserve_batch([value], expires_at=deadline())
    result = await issuer.reserve_batch([value], expires_at=deadline())
    assert result.permits[0].decision is ReserveDecision.REPLAY and result.proofs == ()
    assert len(persistence.calls) == retained.entries == 1
    assert cursors.available_permits == 3


@pytest.mark.parametrize("bound", ["cursor", "receipt"])
async def test_capacity_failure_occurs_before_funding_or_warm_issue(bound):
    persistence, cursors, retained, issuer = state(
        cursor_entries=1 if bound == "cursor" else 1024,
        receipt_entries=1 if bound == "receipt" else 1024,
    )
    proof = terminal().receipt
    subject = PermitSubject.from_reservation(proof.reservation)
    assert cursors.add(subject, proof.grant)
    with pytest.raises(DurableBatchFull):
        await issuer.reserve_batch(items(5, proof.reservation), expires_at=deadline())
    assert persistence.calls == [] and retained.entries == 0
    assert cursors.get(subject).next_ordinal == 0 and cursors.available_permits == 4


async def test_caller_mutation_during_funding_cannot_change_retained_facts():
    persistence = Funding()
    persistence.continue_funding = asyncio.Event()
    _, _, retained, issuer = state(persistence)
    value = items(1)[0]
    value.pricing_snapshot["nested"] = {"price": "before"}
    task = asyncio.create_task(issuer.reserve_batch([value], expires_at=deadline()))
    await persistence.entered.wait()
    value.pricing_snapshot["nested"]["price"] = "after"
    persistence.continue_funding.set()
    await task
    assert retained.get(value.operation_id).reservation.pricing_snapshot["nested"] == {
        "price": "before"
    }


@pytest.mark.parametrize(
    "decision", [ReserveDecision.BUDGET_EXHAUSTED, ReserveDecision.CAPACITY_EXHAUSTED]
)
async def test_denials_create_no_dispatch_and_no_retained_financial_charge(decision):
    persistence, cursors, retained, issuer = state(Funding(decision=decision))
    result = await issuer.reserve_batch(items(3), expires_at=deadline())
    assert len(persistence.calls) <= 2
    assert all(permit.decision is decision for permit in result.permits)
    assert result.proofs == () and cursors.entries == retained.entries == 0


@pytest.mark.parametrize("failure", ["duplicate", "stale", "oversized"])
async def test_invalid_input_never_funds_or_enters_admission(failure):
    persistence, cursors, retained, issuer = state()
    values = items(1)
    if failure == "duplicate":
        values += values
    elif failure == "stale":
        values[0] = values[0].model_copy(update={"protocol_generation": 8})
    else:
        values = items(257)
    with pytest.raises(ValueError):
        await issuer.reserve_batch(values, expires_at=deadline())
    assert persistence.calls == [] and cursors.entries == retained.entries == 0
    assert not issuer.admission.active


@pytest.mark.parametrize("target", [0, 1025, True])
async def test_invalid_target_cannot_create_an_issuer(target):
    _, cursors, retained, _ = state()
    with pytest.raises(ValueError):
        LocalPermitIssuer(Funding(), cursors, retained, target_operations=target)


async def test_256_requests_need_no_more_than_256_staged_grants_across_two_rounds():
    persistence, cursors, retained, issuer = state(Funding(maximum=1))
    original = items(1)[0]
    values = [
        fresh(original).model_copy(
            update={
                "attribution": original.attribution.model_copy(
                    update={"model": f"pair-{index // 2}"}
                )
            }
        )
        for index in range(256)
    ]
    result = await issuer.reserve_batch(values, expires_at=deadline())
    assert len(persistence.calls) == 2
    assert [len(call) for call in persistence.calls] == [128, 128]
    assert len(result.proofs) == retained.entries == 256
    assert all(permit.decision is ReserveDecision.DISPATCH for permit in result.permits)
    assert cursors.entries == cursors.staged_grants == cursors.available_permits == 0


@pytest.mark.parametrize("invalid", ["fence", "limit", "count", "shape"])
async def test_invalid_funding_reply_never_consumes_warm_capacity(invalid):
    class InvalidFunding(Funding):
        async def allocate_batch(self, allocations, *, expires_at):
            result = await super().allocate_batch(allocations, expires_at=expires_at)
            if invalid == "fence":
                from uuid import uuid4

                result[0] = result[0].model_copy(update={"fence_token": uuid4()})
            elif invalid == "limit":
                result[0] = result[0].model_copy(update={"operation_limit": 1024})
            elif invalid == "count":
                result = []
            else:
                result[0] = {}
            return result

    _, cursors, retained, issuer = state(InvalidFunding())
    proof = terminal().receipt
    subject = PermitSubject.from_reservation(proof.reservation)
    assert cursors.add(subject, proof.grant)
    with pytest.raises(AccountingProtocolUnavailable):
        await issuer.reserve_batch(items(5, proof.reservation), expires_at=deadline())
    assert cursors.get(subject).next_ordinal == 0 and cursors.available_permits == 4
    assert retained.entries == cursors.staged_grants == cursors.retiring_grants == 0
    assert not issuer.admission.active
