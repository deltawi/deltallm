"""Accounting v2 local stores drain on every pod after load (docs/operations/bounded-state.md).

Each test starts several local accounting runtimes ("pods") on one database,
loads them at once, and checks that every bounded in-memory store returns to
its idle level while the pods keep running. A store that only empties on
shutdown counts as a leak here: prod pods run for days.
"""

import asyncio
from uuid import uuid4

import pytest

from src.billing.accounting.permits.accounting_local_cursors import LocalCursorStore
from src.billing.accounting.permits.accounting_local_issuer import LocalPermitIssuer
from src.billing.accounting.permits.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting.permits.accounting_local_returns import LocalReturnWorker
from src.billing.accounting.accounting_local_runtime import LocalAccountingRuntime
from src.billing.accounting.accounting_local_service import LocalAccountingService
from src.billing.accounting.journal.accounting_local_terminal import LocalTerminalOwner
from src.billing.accounting.journal.accounting_terminal_receipts import JournalReceipt
from src.db.accounting.journal.accounting_journal import AccountingJournalRepository
from src.db.accounting.permits.accounting_local_leases import AccountingLocalLeaseRepository
from src.db.accounting.accounting_protocol import AccountingProtocolRepository
from tests.accounting_adapters.journal_terminal import JournalTerminalPersistence
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_local_service import handle
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _finalization,
    _reservation,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db

PODS = 3
ITEMS_PER_POD = 24
# Several budget windows give several permit subjects, so each pod holds more
# than one funded cursor at a time.
WINDOWS = 3
# The shortest grant the database allows, so idle cursors expire quickly.
GRANT_TTL_SECONDS = 1


class Pod:
    def __init__(self, db, generation: int, name: str) -> None:
        lease = AccountingLocalLeaseRepository(
            db, owner_id=name, grant_ttl_seconds=GRANT_TTL_SECONDS
        )
        self.cursors = LocalCursorStore(
            generation=generation, max_entries=1024, max_retained_bytes=8 * 1024 * 1024
        )
        self.receipts = LocalReceiptStore(max_entries=1024, max_retained_bytes=8 * 1024 * 1024)
        issuer = LocalPermitIssuer(lease, self.cursors, self.receipts, target_operations=4)
        terminal = LocalTerminalOwner(
            JournalTerminalPersistence(AccountingJournalRepository(db)),
            self.receipts,
            generation=generation,
            receipt_type=JournalReceipt,
        )
        self.service = LocalAccountingService(
            AccountingProtocolRepository(db),
            generation=generation,
            issuer=issuer,
            terminal=terminal,
            dwell_seconds=0,
        )
        self.runtime = LocalAccountingRuntime(
            self.service, LocalReturnWorker(lease, self.cursors, issuer, poll_seconds=0.01)
        )

    def state(self) -> dict[str, int]:
        return {
            "receipts": self.receipts.entries,
            "receipt_bytes": self.receipts.retained_bytes,
            "cursors": self.cursors.entries,
            "cursor_bytes": self.cursors.retained_bytes,
            "reservation_queue": self.service.reservations.pending,
            "reservation_queue_bytes": self.service.reservations.retained_bytes,
            "finalization_queue": self.service.finalizations.pending,
            "finalization_queue_bytes": self.service.finalizations.retained_bytes,
        }


IDLE = {
    "receipts": 0,
    "receipt_bytes": 0,
    "cursors": 0,
    "cursor_bytes": 0,
    "reservation_queue": 0,
    "reservation_queue_bytes": 0,
    "finalization_queue": 0,
    "finalization_queue_bytes": 0,
}


async def _wait_for_idle(pods: list[Pod], *, seconds: float) -> list[dict[str, int]]:
    loop = asyncio.get_running_loop()
    until = loop.time() + seconds
    while True:
        states = [pod.state() for pod in pods]
        if all(state == IDLE for state in states) or loop.time() >= until:
            return states
        await asyncio.sleep(0.05)


async def _started_pods(clients, generation: int) -> list[Pod]:
    pods = [
        Pod(clients[index % len(clients)], generation, f"bounded-state-pod-{index}")
        for index in range(PODS)
    ]
    await asyncio.gather(*(pod.runtime.start(expires_at=deadline()) for pod in pods))
    return pods


async def _load(pods: list[Pod], generation: int, windows: list[str]):
    """Reserve ITEMS_PER_POD items on every pod at once; return (pod, handle) pairs."""

    async def reserve(pod: Pod, index: int):
        permit = await pod.service.reserve(_reservation(generation, windows[index % WINDOWS]))
        return pod, handle(permit)

    return await asyncio.gather(
        *(reserve(pod, index) for pod in pods for index in range(ITEMS_PER_POD))
    )


async def _close(pods: list[Pod]) -> None:
    await asyncio.gather(
        *(pod.runtime.close(expires_at=deadline()) for pod in pods), return_exceptions=True
    )


async def _windows(db, generation: int) -> list[str]:
    windows = [str(uuid4()) for _ in range(WINDOWS)]
    for window in windows:
        await _create_window(db, generation, window, limit="100000")
    return windows


async def test_every_pod_drains_its_local_stores_when_it_finalizes_its_own_work(accounting_db):
    """API shape: the pod that reserved an operation also finalizes it."""
    clients, generation = accounting_db
    windows = await _windows(clients[0], generation)
    pods = await _started_pods(clients, generation)
    try:
        issued = await _load(pods, generation, windows)
        assert sum(pod.receipts.entries for pod in pods) == PODS * ITEMS_PER_POD
        await asyncio.gather(
            *(
                pod.service.finalize_operation(operation, _finalization(operation.reservation))
                for pod, operation in issued
            )
        )
        # Receipts leave at once; unused cursor suffixes leave once their
        # grant's dispatch horizon passes and the return worker hands them back.
        states = await _wait_for_idle(pods, seconds=GRANT_TTL_SECONDS + 5)
        assert states == [IDLE] * PODS
    finally:
        await _close(pods)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "Known leak: a local receipt is released only by the pod that issued it, so "
        "batch work finalized by another pod stays until restart. The fix that "
        "releases it from the journal is tracked separately; remove this marker with it."
    ),
)
async def test_every_pod_drains_its_receipts_when_another_pod_finalizes_its_work(accounting_db):
    """Batch shape: the completion outbox finalizes on whichever pod claims the row."""
    clients, generation = accounting_db
    windows = await _windows(clients[0], generation)
    pods = await _started_pods(clients, generation)
    try:
        issued = await _load(pods, generation, windows)
        await asyncio.gather(
            *(
                pods[(pods.index(pod) + 1) % PODS].service.finalize_operation(
                    operation, _finalization(operation.reservation)
                )
                for pod, operation in issued
            )
        )
        states = await _wait_for_idle(pods, seconds=GRANT_TTL_SECONDS + 5)
        assert states == [IDLE] * PODS
    finally:
        await _close(pods)
