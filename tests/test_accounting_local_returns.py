"""Bulk return failures retain every suffix and its memory charge."""

import asyncio
from datetime import timedelta

import pytest

from src.billing.accounting_local_returns import LocalReturnWorker
from src.billing.accounting_local_issue import LocalIssueCommit
from src.billing.accounting_local_leases import LocalPermitReceipt, LocalPermitReturn
from src.billing.preissued_permits import PermitSubject
from src.billing.durable_microbatch import DurableBatchClosed
from src.db.accounting_permit_results import invalid_result
from src.telemetry.lifecycle import WorkerState
from tests.test_accounting_local_cursors import value
from tests.test_accounting_local_issue import deadline
from tests.test_accounting_local_issuer import Funding, items, state
from tests.test_accounting_local_leases import terminal


class Returns:
    def __init__(self, *, error=None, change=None):
        self.error = error
        self.change = change
        self.calls = []
        self.entered = asyncio.Event()
        self.resume = None

    async def return_batch(self, values, *, expires_at):
        self.calls.append(tuple(values))
        self.entered.set()
        if self.resume is not None:
            await self.resume.wait()
        if self.error is not None:
            raise self.error
        counts = [item.grant.operation_limit - item.first_unused_ordinal for item in values]
        return counts if self.change is None else self.change(counts)


def worker_state(persistence=None, *, count=2):
    _, cursors, retained, issuer = state()
    for _ in range(count):
        assert cursors.add(*value())
    cursors.retire_slice()
    persistence = persistence or Returns()
    worker = LocalReturnWorker(persistence, cursors, issuer, poll_seconds=0.01)
    return persistence, cursors, retained, issuer, worker


async def test_one_bulk_return_releases_only_unused_suffixes_after_complete_ack():
    persistence, cursors, _, issuer, worker = worker_state(count=64)
    assert await worker.run_once(expires_at=deadline()) == 64
    assert len(persistence.calls) == 1 and len(persistence.calls[0]) == 64
    assert cursors.entries == cursors.retained_bytes == 0
    assert not issuer.admission.active


@pytest.mark.parametrize("failure", ["count", "length", "bool", "error", "cancel"])
async def test_bad_or_missing_complete_ack_keeps_all_suffixes(failure):
    changes = {
        "count": lambda counts: counts[:-1] + [0],
        "length": lambda counts: counts[:-1],
        "bool": lambda counts: counts[:-1] + [True],
    }
    persistence = Returns(
        error=invalid_result() if failure == "error" else None, change=changes.get(failure)
    )
    _, cursors, _, issuer, worker = worker_state(persistence)
    charge = cursors.retained_bytes
    if failure == "cancel":
        persistence.resume = asyncio.Event()
        task = asyncio.create_task(worker.run_once(expires_at=deadline()))
        await persistence.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(RuntimeError if failure == "error" else ValueError):
            await worker.run_once(expires_at=deadline())
    assert cursors.entries == cursors.retiring_grants == 2
    assert cursors.retained_bytes == charge and not issuer.admission.active


async def test_return_call_uses_the_callers_deadline_even_if_transport_ignores_it():
    persistence = Returns()
    persistence.resume = asyncio.Event()
    _, cursors, _, issuer, worker = worker_state(persistence)
    charge = cursors.retained_bytes
    with pytest.raises(TimeoutError):
        await worker.run_once(expires_at=asyncio.get_running_loop().time() + 0.02)
    assert cursors.retiring_grants == 2 and cursors.retained_bytes == charge
    assert not issuer.admission.active


async def test_return_tick_does_not_queue_or_touch_staged_grants_during_funding():
    funding = Funding()
    funding.continue_funding = asyncio.Event()
    _, cursors, retained, issuer = state(funding)
    persistence = Returns()
    worker = LocalReturnWorker(persistence, cursors, issuer)
    task = asyncio.create_task(issuer.reserve_batch(items(1), expires_at=deadline()))
    await funding.entered.wait()
    assert await worker.run_once(expires_at=deadline()) == 0
    assert issuer.admission.waiters == 0 and persistence.calls == []
    funding.continue_funding.set()
    assert len((await task).proofs) == retained.entries == 1
    assert cursors.available_permits == 3


async def test_expiry_returns_unused_suffix_without_removing_issued_proof():
    _, cursors, retained, issuer = state()
    proof = terminal().receipt
    subject = PermitSubject.from_reservation(proof.reservation)
    grant = proof.grant.model_copy(
        update={"dispatch_expires_at": proof.grant.observed_at + timedelta(seconds=0.5)}
    )
    assert cursors.add(subject, grant)
    issued = LocalIssueCommit(cursors, retained, minimum_validity_seconds=0).commit(
        [LocalPermitReceipt(grant=grant, permit_ordinal=0, reservation=proof.reservation)],
        expires_at=deadline(),
    )
    charge = retained.retained_bytes
    worker = LocalReturnWorker(Returns(), cursors, issuer, minimum_validity_seconds=1)
    assert await worker.run_once(expires_at=deadline()) == 1
    assert cursors.entries == 0 and retained.entries == 1
    assert retained.get(proof.reservation.operation_id) == issued.proofs[0].restore()
    assert retained.retained_bytes == charge


async def test_close_stops_admission_and_drains_multiple_bounded_slices():
    persistence, cursors, _, issuer, worker = worker_state(count=300)
    assert cursors.active_subjects == 44 and cursors.retiring_grants == 256
    await worker.start(expires_at=deadline())
    assert await worker.close(expires_at=deadline())
    assert len(persistence.calls) == 2 and max(map(len, persistence.calls)) == 256
    assert cursors.entries == cursors.retained_bytes == 0
    assert worker.worker_health.state is WorkerState.DISABLED
    with pytest.raises(DurableBatchClosed):
        await issuer.reserve_batch(items(1), expires_at=deadline())
    with pytest.raises(RuntimeError, match="cannot restart"):
        await worker.start(expires_at=deadline())
    assert await worker.close(expires_at=deadline())


async def test_unknown_return_failure_marks_unready_and_keeps_proofs_on_close():
    persistence = Returns(error=invalid_result())
    persistence.resume = asyncio.Event()
    _, cursors, _, _, worker = worker_state(persistence)
    charge = cursors.retained_bytes
    await worker.start(expires_at=deadline())
    await persistence.entered.wait()
    persistence.resume.set()
    async with asyncio.timeout(1):
        while worker.worker_health.state is not WorkerState.DEGRADED:
            await asyncio.sleep(0)
    assert worker.worker_health.state is WorkerState.DEGRADED
    assert not worker.worker_health.ready
    assert not await worker.close(expires_at=asyncio.get_running_loop().time() + 0.03)
    assert worker.worker_health.state is WorkerState.FAILED
    assert cursors.entries == 2 and cursors.retained_bytes == charge


async def test_degraded_worker_recovers_when_exact_return_ack_is_available():
    persistence = Returns(error=invalid_result())
    persistence.resume = asyncio.Event()
    _, cursors, _, _, worker = worker_state(persistence)
    await worker.start(expires_at=deadline())
    await persistence.entered.wait()
    persistence.resume.set()
    async with asyncio.timeout(1):
        while worker.worker_health.state is not WorkerState.DEGRADED:
            await asyncio.sleep(0)
    assert worker.worker_health.state is WorkerState.DEGRADED
    persistence.error = None
    async with asyncio.timeout(1):
        while cursors.entries:
            await asyncio.sleep(0.01)
    assert worker.worker_health.ready
    assert await worker.close(expires_at=deadline())


async def test_unexpected_worker_exit_is_failed_not_ready():
    _, cursors, _, _, worker = worker_state(Returns(change=lambda counts: counts[:-1] + [0]))
    with pytest.raises((RuntimeError, ValueError)):
        await worker.start(expires_at=deadline())
    assert worker.task.done() and worker.worker_health.state is WorkerState.FAILED
    assert cursors.retiring_grants == 2
    assert not await worker.close(expires_at=deadline())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"poll_seconds": 0},
        {"poll_seconds": float("nan")},
        {"call_budget_seconds": 6},
        {"minimum_validity_seconds": -1},
    ],
)
async def test_worker_timing_is_bounded(kwargs):
    _, cursors, _, issuer = state()
    with pytest.raises(ValueError):
        LocalReturnWorker(Returns(), cursors, issuer, **kwargs)


async def test_another_issuers_cursor_store_cannot_use_the_wrong_gate():
    _, cursors, _, _ = state()
    _, _, _, issuer = state()
    with pytest.raises(ValueError, match="share"):
        LocalReturnWorker(Returns(), cursors, issuer)


async def test_failed_start_cancels_owned_task_and_stops_admission():
    _, cursors, _, issuer, worker = worker_state(Returns(error=invalid_result()))
    with pytest.raises(RuntimeError, match="did not start"):
        await worker.start(expires_at=deadline())
    assert worker.task.done() and worker.worker_health.state is WorkerState.FAILED
    assert cursors.retiring_grants == 2 and not issuer.admission.active
    with pytest.raises(DurableBatchClosed):
        await issuer.reserve_batch(items(1), expires_at=deadline())


@pytest.mark.parametrize("values", ["duplicate", "unknown"])
async def test_complete_return_ack_rejects_bad_identity_before_any_removal(values):
    _, cursors, _, _, _ = worker_state()
    proofs = cursors.return_candidates()
    charge = cursors.retained_bytes
    if values == "duplicate":
        selected = (proofs[0], proofs[0])
    else:
        selected = (proofs[0], LocalPermitReturn(grant=value()[1], first_unused_ordinal=0))
    with pytest.raises(ValueError):
        cursors.acknowledge_returns(selected, (4, 4))
    assert cursors.retiring_grants == 2 and cursors.retained_bytes == charge
