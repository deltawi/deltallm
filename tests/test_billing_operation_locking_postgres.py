import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest

from src.billing.operation_reservation import BillingOperationUnavailable, ComponentState
from src.db import billing_operations
from src.db.billing_operations import BillingOperationRepository
from tests import billing_operation_fixtures as fixtures
from tests.test_billing_operations_postgres import deadline, hold

pytestmark = [pytest.mark.integration, pytest.mark.postgres]
selector_billing_db = fixtures.selector_billing_db
operation_db = fixtures.operation_db
review_operation_db = fixtures.review_operation_db


@pytest.fixture(autouse=True)
def billing_invariant_transaction_budget(monkeypatch):
    # This module verifies SQL invariants and lock ordering, not shared-runner
    # throughput within the production 250-ms cap. Bound every functional
    # transaction consistently; the explicit 30-ms caller-deadline test below
    # still expires earlier. Hermetic repository tests assert the production cap.
    monkeypatch.setattr(billing_operations, "DB_BUDGET_SECONDS", 2)


async def test_waiting_reservation_does_not_block_same_key_settlement(
    review_operation_db, monkeypatch
):
    db, old, charge = review_operation_db
    before = await fixtures.capacity(db)
    repository = BillingOperationRepository(db)
    await repository.reserve(old, expires_at=deadline())
    await fixtures.expire(db, old)
    new, _ = fixtures.another_operation(old, charge)
    inserted = asyncio.Event()
    original_insert = repository._insert

    async def observe_insert(tx, operation):
        result = await original_insert(tx, operation)
        inserted.set()
        return result

    monkeypatch.setattr(repository, "_insert", observe_insert)
    task = None
    try:
        async with db.tx(timeout=timedelta(seconds=2)) as tx:
            await tx.query_raw(
                "SELECT operation_id FROM deltallm_billing_operations WHERE operation_id=$1 FOR UPDATE",
                str(old.attribution.operation_id),
            )
            await tx.query_raw(
                "SELECT token FROM deltallm_verificationtoken WHERE token=$1 FOR UPDATE",
                old.attribution.api_key,
            )
            task = asyncio.create_task(repository.reserve(new, expires_at=deadline()))
            await asyncio.wait_for(inserted.wait(), 1)
            # Old implementation held global capacity and waited for our account
            # lock here. Reconciliation then waited for its global capacity lock.
            await tx.execute_raw(
                "SELECT deltallm_recover_operation($1)", str(old.attribution.operation_id)
            )
        assert (await task).selector_state is ComponentState.RESERVED
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert await hold(db, new) == new.total_allowance
    assert await fixtures.reserved_totals(db, new) == [new.total_allowance] * 5
    assert (await fixtures.operation_row(db, old))["closed_at"] is not None
    assert await fixtures.capacity(db) == before + 1


async def test_duplicate_reservation_does_not_hold_capacity_while_waiting_for_operation(
    review_operation_db,
):
    db, operation, _ = review_operation_db
    before = await fixtures.capacity(db)
    repository = BillingOperationRepository(db)
    await repository.reserve(operation, expires_at=deadline())
    await fixtures.expire(db, operation)
    task = None
    try:
        async with db.tx(timeout=timedelta(seconds=2)) as tx:
            await tx.query_raw(
                "SELECT operation_id FROM deltallm_billing_operations WHERE operation_id=$1 FOR UPDATE",
                str(operation.attribution.operation_id),
            )
            pid = (await tx.query_raw("SELECT pg_backend_pid() AS pid"))[0]["pid"]
            task = asyncio.create_task(repository.reserve(operation, expires_at=deadline()))
            await fixtures.wait_for_blocked_transaction(db, pid)
            await tx.execute_raw(
                "SELECT deltallm_recover_operation($1)", str(operation.attribution.operation_id)
            )
        assert (await task).selector_state is ComponentState.UNATTEMPTED
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert await hold(db, operation) == 0
    assert await fixtures.capacity(db) == before


async def test_identical_concurrent_reservations_use_one_hold_and_capacity_slot(
    review_operation_db,
):
    db, operation, _ = review_operation_db
    before = await fixtures.capacity(db)
    repositories = [BillingOperationRepository(db), BillingOperationRepository(db)]
    results = await asyncio.gather(
        *(repositories[index % 2].reserve(operation, expires_at=deadline()) for index in range(8))
    )
    assert all(result.selector_state is ComponentState.RESERVED for result in results)
    assert await hold(db, operation) == operation.total_allowance
    assert await fixtures.capacity(db) == before + 1


async def test_contended_duplicate_timeout_keeps_one_hold_and_capacity_slot(review_operation_db):
    db, operation, _ = review_operation_db
    before = await fixtures.capacity(db)
    repository = BillingOperationRepository(db)
    await repository.reserve(operation, expires_at=deadline())
    async with db.tx(timeout=timedelta(seconds=2)) as tx:
        await tx.query_raw(
            "SELECT operation_id FROM deltallm_billing_operations WHERE operation_id=$1 FOR UPDATE",
            str(operation.attribution.operation_id),
        )
        with pytest.raises(BillingOperationUnavailable):
            await repository.reserve(operation, expires_at=asyncio.get_running_loop().time() + 0.03)
    assert await hold(db, operation) == operation.total_allowance
    assert await fixtures.reserved_totals(db, operation) == [operation.total_allowance] * 5
    assert await fixtures.capacity(db) == before + 1
    assert (await repository.reserve(operation, expires_at=deadline())).selector_state is (
        ComponentState.RESERVED
    )
    assert await fixtures.capacity(db) == before + 1


async def test_concurrent_conflicting_owner_cannot_reuse_operation(review_operation_db):
    db, operation, _ = review_operation_db
    changed = operation.model_copy(update={"owner_token": uuid4()})
    before = await fixtures.capacity(db)
    results = await asyncio.gather(
        BillingOperationRepository(db).reserve(operation, expires_at=deadline()),
        BillingOperationRepository(db).reserve(changed, expires_at=deadline()),
        return_exceptions=True,
    )
    assert sum(isinstance(result, BillingOperationUnavailable) for result in results) == 1
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert await fixtures.reserved_totals(db, operation) == [operation.total_allowance] * 5
    assert await fixtures.capacity(db) == before + 1


async def test_full_capacity_rolls_back_new_operation_and_all_holds(
    review_operation_db, monkeypatch
):
    db, first, charge = review_operation_db
    repository = BillingOperationRepository(db)
    await repository.reserve(first, expires_at=deadline())
    used = await fixtures.capacity(db)
    second, _ = fixtures.another_operation(first, charge)
    query_raw = type(db).query_raw
    capacity_results = []

    async def observe_capacity(tx, query, *args, **kwargs):
        capacity_update = query.startswith("UPDATE deltallm_telemetry_ingestion_capacity ")
        if capacity_update:
            # Prove the transaction reached capacity admission with every new
            # hold applied; an unrelated unavailable error must not pass this test.
            assert (
                await fixtures.reserved_totals(tx, second)
                == [first.total_allowance + second.total_allowance] * 5
            )
        rows = await query_raw(tx, query, *args, **kwargs)
        if capacity_update:
            capacity_results.append(rows)
        return rows

    monkeypatch.setattr(type(db), "query_raw", observe_capacity)
    with pytest.raises(BillingOperationUnavailable):
        await BillingOperationRepository(db, max_pending_operations=used).reserve(
            second, expires_at=deadline()
        )
    assert capacity_results == [[]]
    assert await hold(db, first) == first.total_allowance
    assert await fixtures.reserved_totals(db, first) == [first.total_allowance] * 5
    assert await fixtures.capacity(db) == used
    assert not await db.query_raw(
        "SELECT operation_id FROM deltallm_billing_operations WHERE operation_id=$1",
        str(second.attribution.operation_id),
    )


async def test_recovery_never_holds_spend_capacity_while_waiting_for_account(review_operation_db):
    db, operation, charge = review_operation_db
    repository = BillingOperationRepository(db)
    await repository.reserve(operation, expires_at=deadline())
    await repository.dispatch(operation, component="selector", expires_at=deadline())
    await repository.accept_selector(operation, charge, expires_at=deadline())
    await fixtures.expire(db, operation)
    task = None
    try:
        async with db.tx(timeout=timedelta(seconds=2)) as tx:
            await tx.query_raw(
                "SELECT token FROM deltallm_verificationtoken WHERE token=$1 FOR UPDATE",
                operation.attribution.api_key,
            )
            pid = (await tx.query_raw("SELECT pg_backend_pid() AS pid"))[0]["pid"]
            task = asyncio.create_task(fixtures.recovery(db)._recover_one("receipts"))
            await fixtures.wait_for_blocked_transaction(db, pid)
            # Mirrors canonical spend completion after its ledger update. The old
            # recovery path enqueued first and already owned this capacity row.
            await tx.execute_raw(
                "UPDATE deltallm_telemetry_ingestion_capacity SET updated_at=NOW() WHERE queue_name='spend'"
            )
        assert not (await task).unavailable
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert await hold(db, operation) == operation.selector.allowance
