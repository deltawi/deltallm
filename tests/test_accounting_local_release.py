"""Proofs another process finalized leave the issuing process's bounded store."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.billing.accounting.accounting_local_service import LocalAccountingService
from src.billing.accounting.durable_microbatch import DurableBatchFull
from src.billing.accounting.journal.accounting_local_terminal import LocalTerminalOwner
from src.billing.accounting.journal.accounting_terminal_receipts import JournalReceipt
from src.billing.accounting.journal.accounting_terminal_snapshots import (
    freeze_terminal_snapshots,
)
from src.billing.accounting.permits.accounting_local_cursors import LocalCursorStore
from src.billing.accounting.permits.accounting_local_issuer import LocalPermitIssuer
from src.billing.accounting.permits.accounting_local_leases import LocalPermitFinalization
from src.billing.accounting.permits.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting.permits.accounting_local_release import LocalReceiptReleaseWorker
from src.bootstrap.accounting_local import ApiAccountingRuntime
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from src.db.runtime.telemetry_acceptance import AcceptanceFailure
from src.metrics.accounting import (
    accounting_local_receipt_fill,
    accounting_local_receipt_releases,
    accounting_local_receipts,
)
from src.telemetry.lifecycle import WorkerHealth, WorkerState
from tests.test_accounting_local_issue import deadline
from tests.test_accounting_local_issuer import items
from tests.test_accounting_local_receipts import Clock
from tests.test_accounting_local_service import FundingOwner, handle
from tests.test_accounting_protocol import finalization
from tests.test_accounting_request_path import _AccountingRepository


class Journal:
    """The request pods' shared terminal journal, seen by every issuing process."""

    def __init__(self):
        self.rows = {}
        self.lookups = []
        self.error = None

    async def finalize_batch(self, values, *, expires_at):
        results = []
        for value in freeze_terminal_snapshots(values, generation=7):
            receipt = JournalReceipt(
                protocol_generation=7,
                operation_id=value.operation_id,
                journal_sequence=len(self.rows) + 1,
                outcome=value.outcome,
            )
            self.rows[value.operation_id] = (value.retained_receipt.terminal_identity(), receipt)
            results.append(receipt)
        return results

    async def lookup_terminals(self, values, *, expires_at):
        self.lookups.append(tuple(value.operation_id for value in values))
        if self.error is not None:
            raise self.error
        results = []
        for proof in values:
            row = self.rows.get(proof.operation_id)
            if row is None or row[0] != proof.terminal_identity():
                results.append(None)
            else:
                results.append(row[1].model_copy(update={"replayed": True}))
        return results


def process(journal, *, clock=None, max_entries=1024):
    cursors = LocalCursorStore(generation=7, max_entries=1024, max_retained_bytes=8 * 1024 * 1024)
    receipts = LocalReceiptStore(
        max_entries=max_entries,
        max_retained_bytes=8 * 1024 * 1024,
        **({} if clock is None else {"clock": clock}),
    )
    issuer = LocalPermitIssuer(FundingOwner(), cursors, receipts, target_operations=32)
    terminal = LocalTerminalOwner(journal, receipts, generation=7, receipt_type=JournalReceipt)
    service = LocalAccountingService(
        _AccountingRepository(), generation=7, issuer=issuer, terminal=terminal, dwell_seconds=0
    )
    return receipts, service


def worker(journal, receipts, **settings):
    return LocalReceiptReleaseWorker(journal, receipts, recheck_seconds=10, **settings)


def released(reason):
    return accounting_local_receipt_releases.labels(reason=reason)._value.get()


async def test_operation_finalized_by_another_process_releases_the_issuing_proof():
    journal, clock = Journal(), Clock()
    issuing_receipts, issuing = process(journal, clock=clock, max_entries=3)
    finalizing_receipts, finalizing = process(journal)
    release = worker(journal, issuing_receipts)
    issuing.start()
    finalizing.start()
    try:
        permits = [await issuing.reserve(item) for item in items(3)]
        # Another batch worker delivers the completion of all three operations.
        for permit in permits[:2]:
            operation = handle(permit)
            await finalizing.finalize_operation(operation, finalization(operation.reservation))
        assert issuing_receipts.entries == 3 and finalizing_receipts.entries == 0
        with pytest.raises(DurableBatchFull, match="receipt capacity is full"):
            await issuing.issuer.reserve_batch(items(1), expires_at=deadline())
        before = released("finalized")
        # A recent proof is still in flight, so it is not looked up yet.
        assert await release.run_once(expires_at=deadline()) == 0
        assert journal.lookups == []
        clock.now += 10
        assert await release.run_once(expires_at=deadline()) == 3
        assert len(journal.lookups) == 1 and len(journal.lookups[0]) == 3
        assert issuing_receipts.entries == 1
        assert released("finalized") == before + 2
        assert accounting_local_receipts._value.get() == 1
        assert accounting_local_receipt_fill._value.get() == pytest.approx(1 / 3)
        # The freed capacity serves new work again.
        assert len((await issuing.issuer.reserve_batch(items(1), expires_at=deadline())).proofs)
        # The third operation is checked again only after the recheck interval.
        assert await release.run_once(expires_at=deadline()) == 0
        operation = handle(permits[2])
        await finalizing.finalize_operation(operation, finalization(operation.reservation))
        clock.now += 10
        assert await release.run_once(expires_at=deadline()) == 2
        assert issuing_receipts.entries == 1
    finally:
        await issuing.close()
        await finalizing.close()


async def test_grant_expiry_releases_without_a_lookup():
    journal, clock = Journal(), Clock()
    receipts, service = process(journal, clock=clock)
    service.start()
    try:
        permit = await service.reserve(items(1)[0])
        deadline_at = permit.proof.grant.recovery_deadline
        loop_now = [deadline_at + 59]
        release = worker(journal, receipts, expiry_grace_seconds=60, clock=lambda: loop_now[0])
        before = released("expired")
        clock.now += 10
        assert await release.run_once(expires_at=deadline()) == 1
        assert receipts.entries == 1 and len(journal.lookups) == 1
        loop_now[0] = deadline_at + 60
        clock.now += 10
        assert await release.run_once(expires_at=deadline()) == 1
        assert receipts.entries == 0 and len(journal.lookups) == 1
        assert released("expired") == before + 1
    finally:
        await service.close()


async def test_lookup_failure_keeps_proofs_and_retries_without_stopping():
    journal = Journal()
    receipts, service = process(journal)
    release = LocalReceiptReleaseWorker(journal, receipts, poll_seconds=0.01, recheck_seconds=0.01)
    service.start()
    try:
        operation = handle(await service.reserve(items(1)[0]))
        # Another process finalizes it while this process cannot reach the journal.
        await journal.finalize_batch(
            [
                LocalPermitFinalization(
                    receipt=operation.proof, finalization=finalization(operation.reservation)
                )
            ],
            expires_at=deadline(),
        )
        journal.error = AccountingProtocolUnavailable(AcceptanceFailure.CONNECTION)
        release.start()
        async with asyncio.timeout(2):
            while release.worker_health.state is not WorkerState.DEGRADED:
                await asyncio.sleep(0.01)
        assert release.worker_health == WorkerHealth(WorkerState.DEGRADED, "lookup_unavailable")
        assert receipts.entries == 1
        journal.error = None
        async with asyncio.timeout(2):
            while receipts.entries:
                await asyncio.sleep(0.01)
        assert release.worker_health.state is WorkerState.READY
        assert await release.close(expires_at=deadline())
        assert release.worker_health.state is WorkerState.DISABLED
        with pytest.raises(RuntimeError, match="restart"):
            release.start()
    finally:
        await release.close(expires_at=deadline())
        await service.close()


async def test_mismatched_lookup_reply_releases_nothing():
    journal, clock = Journal(), Clock()
    receipts, service = process(journal, clock=clock)
    service.start()
    try:
        await service.reserve(items(1)[0])
        journal.lookup_terminals = AsyncMock(return_value=[])
        clock.now += 10
        with pytest.raises(AccountingProtocolUnavailable):
            await worker(journal, receipts).run_once(expires_at=deadline())
        assert receipts.entries == 1
    finally:
        await service.close()


@pytest.mark.parametrize(
    "settings",
    [
        {"poll_seconds": 0},
        {"recheck_seconds": float("inf")},
        {"expiry_grace_seconds": -1},
        {"call_budget_seconds": 6},
        {"batch_size": 257},
        {"batch_size": True},
    ],
)
def test_release_settings_are_bounded(settings):
    receipts = LocalReceiptStore(max_entries=1, max_retained_bytes=8192)
    with pytest.raises(ValueError, match="invalid"):
        LocalReceiptReleaseWorker(Journal(), receipts, **settings)


async def test_api_runtime_health_and_lifecycle_keep_releases_out_of_admission():
    order = []
    local = MagicMock()
    local.worker_health = WorkerHealth(WorkerState.READY)
    local.start = AsyncMock(side_effect=lambda **_: order.append("local.start"))
    local.close = AsyncMock(side_effect=lambda **_: order.append("local.close") or True)
    monitor = MagicMock()
    monitor.worker_health = WorkerHealth(WorkerState.READY)
    monitor.start = AsyncMock(side_effect=lambda **_: order.append("monitor.start"))
    monitor.close = AsyncMock(side_effect=lambda **_: order.append("monitor.close") or True)
    releases = MagicMock()
    releases.worker_health = WorkerHealth(WorkerState.DEGRADED, "lookup_unavailable")
    releases.start = MagicMock(side_effect=lambda: order.append("releases.start"))
    releases.close = AsyncMock(side_effect=lambda **_: order.append("releases.close") or True)
    runtime = ApiAccountingRuntime(local, monitor, MagicMock(), releases)
    await runtime.start(expires_at=deadline())
    assert runtime.worker_health.ready
    await runtime.close(expires_at=deadline())
    assert order == [
        "monitor.start",
        "local.start",
        "releases.start",
        "local.close",
        "releases.close",
        "monitor.close",
    ]
