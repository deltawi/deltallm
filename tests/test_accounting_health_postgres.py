"""Durable terminal health uses the same counters that charge native work."""

from decimal import Decimal

import pytest

from src.billing.accounting.health.accounting_health import (
    AccountingBacklogPolicy,
    AccountingBacklogProbe,
)
from src.billing.accounting.journal.accounting_journal_claims import JournalFailure
from src.db.accounting_health import AccountingBacklogRepository
from src.db.accounting_calls import AccountingProtocolUnavailable
from tests.test_accounting_journal_worker_postgres import pending, worker
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_permits_postgres import CountingClient
from tests.test_accounting_protocol_postgres import (
    _settle_grants,
    _window,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


def repository(db):
    return AccountingBacklogRepository(db, statement_budget_seconds=2)


async def test_empty_without_capacity_rows_is_verified_not_a_missing_generation(accounting_db):
    clients, generation = accounting_db
    counted = CountingClient(clients[0])
    repo = repository(counted)
    value = await repo.snapshot(generation=generation, expires_at=deadline())
    assert value.sampled_drained and value.pending_entries == value.pending_bytes == 0
    assert value.oldest_age_seconds is None and value.partition_count == 4
    assert counted.calls == 1
    with pytest.raises(AccountingProtocolUnavailable):
        await repo.snapshot(generation=2**63 - 1, expires_at=deadline())
    assert counted.calls == 2


async def test_accept_processing_commit_and_settlement_have_distinct_exact_snapshots(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window, _, _, _ = await pending(db, generation)
    repo, processor = repository(db), worker(db)
    accepted = await repo.snapshot(generation=generation, expires_at=deadline())
    assert accepted.pending_entries == accepted.outstanding_operations == 4
    assert accepted.pending_bytes > 0 and accepted.failed_entries == 0
    assert accepted.oldest_age_seconds is not None and not accepted.sampled_drained
    claim = await processor.claim(generation=generation, worker_id="health", expires_at=deadline())
    processing = await repo.snapshot(generation=generation, expires_at=deadline())
    assert processing.pending_entries == accepted.pending_entries
    assert processing.pending_bytes == accepted.pending_bytes
    assert await processor.materialize(claim, expires_at=deadline()) == 4
    committed = await repo.snapshot(generation=generation, expires_at=deadline())
    assert committed.pending_entries == committed.pending_bytes == 0
    assert committed.outstanding_operations == 4 and not committed.sampled_drained
    assert committed.oldest_age_seconds is None
    assert await _settle_grants(db, generation) == 1
    assert (await repo.snapshot(generation=generation, expires_at=deadline())).sampled_drained
    assert await _window(db, window) == (Decimal("2.4"), Decimal(0), Decimal(0))


async def test_dead_letters_remain_funded_and_unready_without_a_full_journal_count(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window, _, _, _ = await pending(db, generation)
    processor = worker(db)
    for _ in range(5):
        claim = await processor.claim(
            generation=generation, worker_id="health", expires_at=deadline()
        )
        assert await processor.fail(claim, JournalFailure.PERSISTENCE, expires_at=deadline()) == 4
    counted = CountingClient(db)
    runtime = AccountingBacklogProbe(
        repository(counted), AccountingBacklogPolicy(generation=generation)
    )
    assert not await runtime.refresh(expires_at=deadline())
    assert runtime.worker_health.detail == "terminal_failed" and counted.calls == 1
    assert runtime.snapshot.failed_entries == runtime.snapshot.pending_entries == 4
    assert runtime.snapshot.pending_bytes > 0 and not runtime.snapshot.sampled_drained
    assert await _settle_grants(db, generation) == 0
    assert await _window(db, window) == (Decimal(0), Decimal(4), Decimal(0))


@pytest.mark.parametrize("kind", ["pending", "processing", "failed"])
async def test_oldest_age_includes_each_unsettled_state(accounting_db, kind):
    clients, generation = accounting_db
    db = clients[0]
    await pending(db, generation)
    if kind != "pending":
        processor = worker(db)
        for _ in range(5 if kind == "failed" else 1):
            claim = await processor.claim(
                generation=generation, worker_id="health", expires_at=deadline()
            )
            if kind == "failed":
                assert (
                    await processor.fail(claim, JournalFailure.PERSISTENCE, expires_at=deadline())
                    == 4
                )
    await db.execute_raw(
        "UPDATE deltallm_accounting_terminal_journal SET accepted_at=NOW()-INTERVAL '120 seconds' WHERE generation=$1",
        generation,
    )
    runtime = AccountingBacklogProbe(repository(db), AccountingBacklogPolicy(generation=generation))
    assert not await runtime.refresh(expires_at=deadline())
    assert 120 <= runtime.snapshot.oldest_age_seconds < 125
    assert runtime.worker_health.detail == (
        "terminal_failed" if kind == "failed" else "terminal_age_limit"
    )


async def test_one_full_partition_is_visible_even_with_other_empty_partitions(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    await pending(db, generation)
    await db.execute_raw(
        "UPDATE deltallm_accounting_terminal_capacity SET max_entries=pending_entries WHERE generation=$1",
        generation,
    )
    runtime = AccountingBacklogProbe(repository(db), AccountingBacklogPolicy(generation=generation))
    assert not await runtime.refresh(expires_at=deadline())
    assert (
        runtime.snapshot.capacity_saturated and runtime.worker_health.detail == "terminal_capacity"
    )


@pytest.mark.parametrize("change", ["missing", "extra", "misnumbered"])
async def test_bad_partition_coverage_is_unavailable_not_an_invented_empty_backlog(
    accounting_db, change
):
    clients, generation = accounting_db
    db = clients[0]
    if change != "extra":
        await db.execute_raw(
            "DELETE FROM deltallm_accounting_partitions WHERE generation=$1 AND partition_id=3",
            generation,
        )
    if change != "missing":
        await db.execute_raw(
            "INSERT INTO deltallm_accounting_partitions(protocol_name,generation,partition_id,max_outstanding) VALUES ('primary',$1,4,1000)",
            generation,
        )
    with pytest.raises(AccountingProtocolUnavailable):
        await repository(db).snapshot(generation=generation, expires_at=deadline())
