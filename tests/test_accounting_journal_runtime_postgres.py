"""The supervised worker keeps exact financial effects after interrupted replies."""

import asyncio
from decimal import Decimal

import pytest

from src.billing.accounting_journal_runtime import JournalProcessingWorker, JournalWorkerConfig
from src.db.accounting_journal_worker import AccountingJournalWorkerRepository
from tests.test_accounting_journal_postgres import counts
from tests.test_accounting_journal_worker_postgres import pending
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_permits_postgres import CountingClient
from tests.test_accounting_protocol_postgres import (
    _settle_grants,
    _window,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


def processor(db, generation, *, worker_id="worker"):
    return JournalProcessingWorker(
        AccountingJournalWorkerRepository(db),
        JournalWorkerConfig(generation=generation, worker_id=worker_id),
    )


@pytest.mark.parametrize("phase", [None, "claim", "materialize"])
async def test_worker_tick_recovers_lost_replies_and_applies_exactly_one_charge(
    accounting_db, phase
):
    clients, generation = accounting_db
    db = clients[0]
    window, _, _, _ = await pending(db, generation)

    class Client(CountingClient):
        async def query_raw(self, query, *parameters):
            if (
                phase == "materialize"
                and "SELECT deltallm_accounting_materialize_terminal_journal" in query
            ):
                self.lose_ack = True
            return await super().query_raw(query, *parameters)

    counted = Client(db, lose_ack=phase == "claim")
    runtime = processor(counted, generation)
    assert await runtime.run_once(expires_at=deadline()) == 4
    assert counted.calls == 2 + (phase is not None)
    assert runtime.retained_claim is None and runtime.retained_claim_bytes == 0
    assert await counts(db, generation) == {
        "journal": 4,
        "payloads": 0,
        "operations": 4,
        "charged": 0,
    }
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal("2.4"), Decimal(0), Decimal(0))
    assert await runtime.close(expires_at=deadline())


async def test_cancelled_commit_keeps_handle_and_retries_without_another_claim(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window, _, _, _ = await pending(db, generation)

    class Client(CountingClient):
        cancelled = False

        async def query_raw(self, query, *parameters):
            result = await super().query_raw(query, *parameters)
            if (
                not self.cancelled
                and "SELECT deltallm_accounting_materialize_terminal_journal" in query
            ):
                self.cancelled = True
                raise asyncio.CancelledError()
            return result

    counted = Client(db)
    runtime = processor(counted, generation)
    with pytest.raises(asyncio.CancelledError):
        await runtime.run_once(expires_at=deadline())
    retained = runtime.retained_claim
    assert len(retained.sequences) == 4 and runtime.retained_claim_bytes > 0
    assert await counts(db, generation) == {
        "journal": 4,
        "payloads": 0,
        "operations": 4,
        "charged": 0,
    }
    assert await runtime.run_once(expires_at=deadline()) == 4
    assert counted.calls == 3 and runtime.retained_claim is None
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal("2.4"), Decimal(0), Decimal(0))
    assert await runtime.close(expires_at=deadline())


async def test_cancelled_claim_keeps_money_and_documents_until_lease_recovery(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window, _, _, accepted = await pending(db, generation)

    class Client(CountingClient):
        async def query_raw(self, query, *parameters):
            await super().query_raw(query, *parameters)
            raise asyncio.CancelledError()

    original = processor(Client(db), generation, worker_id="original")
    with pytest.raises(asyncio.CancelledError):
        await original.run_once(expires_at=deadline())
    assert original.retained_claim is None
    assert await counts(db, generation) == {
        "journal": 4,
        "payloads": 4,
        "operations": 0,
        "charged": 4,
    }
    replacement = processor(clients[1], generation, worker_id="replacement")
    assert await replacement.run_once(expires_at=deadline()) == 0
    assert await _settle_grants(db, generation) == 0
    await db.execute_raw(
        "UPDATE deltallm_accounting_terminal_journal SET lease_expires_at=NOW()-INTERVAL '1 second' "
        "WHERE generation=$1 AND sequence=ANY($2::bigint[])",
        generation,
        [ack.journal_sequence for ack in accepted],
    )
    assert await replacement.run_once(expires_at=deadline()) == 4
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal("2.4"), Decimal(0), Decimal(0))
    assert await original.close(expires_at=deadline())
    assert await replacement.close(expires_at=deadline())


async def test_start_and_close_process_durable_work_without_discarding_backlog(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window, _, _, _ = await pending(db, generation)
    runtime = processor(db, generation)
    try:
        await runtime.start(expires_at=deadline())
        assert runtime.worker_health.ready
        assert (await counts(db, generation))["operations"] == 4
    finally:
        assert await runtime.close(expires_at=deadline())
    assert runtime.task.done()
    assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal("2.4"), Decimal(0), Decimal(0))
