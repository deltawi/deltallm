"""Reporting must not skip a terminal event that commits after a larger key."""

import asyncio
import json
import os
from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.accounting.accounting_protocol import AccountingAttempt, AccountingOperationHandle
from src.db.accounting_protocol import AccountingProtocolRepository
from src.billing.accounting.permits.accounting_local_leases import (
    LocalPermitFinalization,
    LocalPermitReceipt,
    LocalPermitReturn,
)
from src.billing.accounting.accounting_protocol import AccountingOutcome
from src.db.accounting_journal import AccountingJournalRepository
from src.db.accounting_journal_worker import AccountingJournalWorkerRepository
from prisma.errors import RawQueryError
from prisma import Prisma
from tests.accounting_read_model_fixtures import reporting_finalization, reporting_handle
from tests.test_accounting_local_leases_postgres import allocation, deadline, funded, owner
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _reservation,
    _settle_grants,
    _window,
    accounting_db as _accounting_db,
)
from tests.test_accounting_read_model_postgres import effects, next_page, repository, source
from tests.performance.accounting_allocator_plans import capture_accounting_plans
from tests.test_accounting_allocator_bounds_postgres import nodes
from tests.test_accounting_journal_worker_postgres import pending

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def terminals(db, generation):
    window = str(uuid4())
    await _create_window(db, generation, window)
    items = [_reservation(generation, window) for _ in range(2)]
    permits = await AccountingProtocolRepository(
        db, statement_budget_seconds=2, grant_target_operations=2
    ).reserve_batch(items, expires_at=deadline())
    assert permits[0].accounting_partition == permits[1].accounting_partition
    values = []
    for item, permit in zip(items, permits, strict=True):
        a = item.attribution
        handle = AccountingOperationHandle(
            reservation=item,
            dispatch_token=permit.dispatch_token,
            accounting_partition=permit.accounting_partition,
            attempts=(
                AccountingAttempt(
                    deployment_id=a.deployment_id,
                    provider=a.provider,
                    model=a.model,
                    pricing_snapshot=item.pricing_snapshot,
                ),
            ),
        )
        values.append(reporting_finalization(handle))
    return values


async def finalize(db, generation, value):
    return await db.query_raw(
        "SELECT * FROM deltallm_accounting_finalize_grant_batch($1,$2::jsonb)",
        generation,
        json.dumps([value.model_dump(mode="json")]),
    )


async def project(db, generation):
    repo = repository(db)
    await repo.initialize(generation=generation, expires_at=deadline())
    for _ in range(4):
        page = await next_page(repo, generation)
        if page is None:
            break
        await repo.materialize(page, expires_at=deadline())


async def test_commit_order_cannot_leave_a_terminal_below_the_reporting_frontier(accounting_db):
    clients, generation = accounting_db
    first, second = clients
    values = await terminals(first, generation)
    task = None
    try:
        async with first.tx() as tx:
            await finalize(tx, generation, values[0])
            task = asyncio.create_task(finalize(second, generation, values[1]))
            # Observe a real lock, not an assumed delay. On the old implementation
            # the second commit finishes and reporting advances past the first.
            async with asyncio.timeout(3):
                while not task.done():
                    rows = await second.query_raw(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname=current_database() AND wait_event='advisory' "
                        "AND query LIKE '%deltallm_accounting_finalize_grant_batch%') AS waiting"
                    )
                    if rows[0]["waiting"]:
                        break
                    await asyncio.sleep(0)
            if task.done():
                await task
                await project(second, generation)
        await task
        await project(second, generation)
        assert await effects(first, generation) == {"facts": 2, "audits": 2, "rolled": 2}
        rows = await first.query_raw(
            "SELECT sum(spend_exact)::text AS spend FROM deltallm_accounting_usage_facts_v2 "
            "WHERE protocol_generation=$1",
            generation,
        )
        assert Decimal(rows[0]["spend"]) == Decimal("1.2")
        # A controlled checkpoint replay can repair older omissions. Its existing
        # facts, audit rows, and rollups must not be counted a second time.
        await first.execute_raw(
            "UPDATE deltallm_accounting_projection_checkpoints SET last_sequence=0 "
            "WHERE projection_name='accounting-read-model-v2' AND generation=$1",
            generation,
        )
        await project(second, generation)
        assert await effects(first, generation) == {"facts": 2, "audits": 2, "rolled": 2}
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_native_journal_cannot_publish_past_an_uncommitted_reconciliation(accounting_db):
    clients, generation = accounting_db
    first, second = clients
    # Exercise the smallest topology so both independent grants share a partition.
    await first.execute_raw(
        "UPDATE deltallm_accounting_protocols SET partition_count=1 WHERE generation=$1",
        generation,
    )
    _, previous = await source(first, generation, count=1, outcome=AccountingOutcome.UNCERTAIN)
    old = previous[0]
    await owner(first).return_batch(
        [LocalPermitReturn(grant=old.receipt.grant, first_unused_ordinal=1)],
        expires_at=deadline(),
    )
    assert await _settle_grants(first, generation) == 1
    _, item, grant = await funded(first, generation)
    receipt = LocalPermitReceipt(grant=grant, permit_ordinal=0, reservation=item)
    terminal = reporting_finalization(reporting_handle(receipt))
    await AccountingJournalRepository(first, statement_budget_seconds=2).append_batch(
        [LocalPermitFinalization(receipt=receipt, finalization=terminal)],
        expires_at=deadline(),
    )
    journal = AccountingJournalWorkerRepository(second, statement_budget_seconds=2)
    claim = await journal.claim(
        generation=generation, worker_id="publication", expires_at=deadline()
    )
    task = None
    try:
        async with first.tx() as tx:
            await tx.query_raw(
                "SELECT deltallm_accounting_resolve_provisional($1,$2,0,'null'::jsonb,$3) AS sequence",
                generation,
                str(old.receipt.reservation.operation_id),
                "provider confirmed no charge",
            )
            task = asyncio.create_task(journal.materialize(claim, expires_at=deadline()))
            async with asyncio.timeout(3):
                while not task.done():
                    rows = await second.query_raw(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname=current_database() "
                        "AND wait_event='advisory' AND query LIKE '%deltallm_accounting_materialize_terminal_journal%') AS waiting"
                    )
                    if rows[0]["waiting"]:
                        break
                    await asyncio.sleep(0)
            assert not task.done(), "Journal publication bypassed reconciliation's event fence"
        assert await task == 1
        await project(second, generation)
        assert await effects(first, generation) == {"facts": 1, "audits": 3, "rolled": 1}
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_direct_expiry_cannot_settle_a_grant_backed_reservation(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    values = await terminals(db, generation)
    await db.execute_raw(
        "UPDATE deltallm_billing_operations SET created_at=NOW()-INTERVAL '10 minutes',"
        "expires_at=NOW()-INTERVAL '1 second' WHERE accounting_generation=$1",
        generation,
    )
    rows = await db.query_raw(
        "SELECT deltallm_accounting_reconcile_expired($1,256) AS count",
        generation,
    )
    assert rows == [{"count": 0}]
    rows = await db.query_raw(
        "SELECT deltallm_accounting_reconcile_expired_grants($1,256) AS count",
        generation,
    )
    assert rows == [{"count": 2}]
    assert await _settle_grants(db, generation) == 1
    window = (
        await db.query_raw(
            "SELECT window_id FROM deltallm_accounting_grant_windows WHERE grant_id IN "
            "(SELECT accounting_grant_id FROM deltallm_billing_operations WHERE operation_id=$1)",
            str(values[0].operation_id),
        )
    )[0]["window_id"]
    assert await _window(db, window) == (Decimal(0), Decimal(0), Decimal(2))
    await project(db, generation)
    assert await effects(db, generation) == {"facts": 0, "audits": 2, "rolled": 0}


async def test_journal_parent_lock_precedes_publication_during_same_window_reconciliation(
    accounting_db,
):
    clients, generation = accounting_db
    first, second = clients
    await first.execute_raw(
        "UPDATE deltallm_accounting_protocols SET partition_count=1 WHERE generation=$1",
        generation,
    )
    window, previous = await source(first, generation, count=1, outcome=AccountingOutcome.UNCERTAIN)
    old = previous[0]
    await owner(first).return_batch(
        [LocalPermitReturn(grant=old.receipt.grant, first_unused_ordinal=1)],
        expires_at=deadline(),
    )
    assert await _settle_grants(first, generation) == 1
    item = _reservation(generation, window)
    grant = (await owner(first).allocate_batch([allocation(item)], expires_at=deadline()))[0]
    receipt = LocalPermitReceipt(grant=grant, permit_ordinal=0, reservation=item)
    await AccountingJournalRepository(first, statement_budget_seconds=2).append_batch(
        [
            LocalPermitFinalization(
                receipt=receipt,
                finalization=reporting_finalization(
                    reporting_handle(receipt), AccountingOutcome.UNCERTAIN
                ),
            )
        ],
        expires_at=deadline(),
    )
    journal = AccountingJournalWorkerRepository(second, statement_budget_seconds=2)
    claim = await journal.claim(
        generation=generation, worker_id="parent-lock", expires_at=deadline()
    )
    resolver = Prisma(datasource={"url": os.environ["DATABASE_URL"]})
    await resolver.connect()
    tasks = []

    async def wait_for_lock(tx, signature):
        async with asyncio.timeout(3):
            while True:
                await tx.execute_raw("SELECT pg_stat_clear_snapshot()")
                rows = await tx.query_raw(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname=current_database() "
                    "AND wait_event_type='Lock' AND query LIKE $1) AS waiting",
                    "%" + signature + "%",
                )
                if rows[0]["waiting"]:
                    return
                assert all(not task.done() for task in tasks)
                await asyncio.sleep(0)

    try:
        async with first.tx() as barrier:
            # Hold publication until both real owners are waiting. This puts the
            # journal first in the publication queue without any clock assumptions.
            await barrier.execute_raw(
                "SELECT deltallm_accounting_lock_event_publication($1,ARRAY[0])", generation
            )
            tasks.append(asyncio.create_task(journal.materialize(claim, expires_at=deadline())))
            await wait_for_lock(barrier, "deltallm_accounting_materialize_terminal_journal")
            tasks.append(
                asyncio.create_task(
                    resolver.query_raw(
                        "SELECT deltallm_accounting_resolve_provisional($1,$2,0,'null'::jsonb,$3) AS sequence",
                        generation,
                        str(old.receipt.reservation.operation_id),
                        "provider confirmed no charge",
                    )
                )
            )
            await wait_for_lock(barrier, "deltallm_accounting_resolve_provisional")
        results = await asyncio.gather(*tasks)
        assert results[0] == 1 and results[1][0]["sequence"] > 0
        await owner(first).return_batch(
            [LocalPermitReturn(grant=grant, first_unused_ordinal=1)], expires_at=deadline()
        )
        assert await _settle_grants(first, generation) == 1
        assert await _window(first, window) == (Decimal(0), Decimal(0), Decimal(1))
        await project(first, generation)
        assert await effects(first, generation) == {"facts": 0, "audits": 3, "rolled": 0}
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await resolver.disconnect()


@pytest.mark.parametrize("rollback", [False, True])
async def test_publication_locks_release_on_commit_or_rollback(accounting_db, rollback):
    clients, generation = accounting_db
    first, second = clients
    task = None

    class Abort(Exception):
        pass

    try:
        try:
            async with first.tx() as tx:
                await tx.execute_raw(
                    "SELECT deltallm_accounting_lock_event_publication($1,ARRAY[1,0,1])",
                    generation,
                )
                # Another partition has no dependency on this transaction.
                await second.execute_raw(
                    "SELECT deltallm_accounting_lock_event_publication($1,ARRAY[2])",
                    generation,
                )
                task = asyncio.create_task(
                    second.execute_raw(
                        "SELECT deltallm_accounting_lock_event_publication($1,ARRAY[0,1])",
                        generation,
                    )
                )
                async with asyncio.timeout(3):
                    while True:
                        rows = await second.query_raw(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE datname=current_database() AND wait_event='advisory' "
                            "AND query LIKE '%deltallm_accounting_lock_event_publication%') AS waiting"
                        )
                        if rows[0]["waiting"]:
                            assert not task.done()
                            break
                        await asyncio.sleep(0)
                if rollback:
                    raise Abort()
        except Abort:
            pass
        await asyncio.wait_for(task, 3)
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_every_terminal_writer_fences_before_allocating_a_sequence(accounting_db):
    clients, _ = accounting_db
    rows = await clients[0].query_raw(
        "SELECT proname,prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname=current_schema() AND proname LIKE 'deltallm_accounting_%' "
        "AND prosrc LIKE '%INSERT INTO deltallm_accounting_events%' "
        "AND prosrc LIKE '%outcome%'"
    )
    assert len(rows) == 6
    for row in rows:
        source = row["prosrc"]
        fence = source.index("PERFORM deltallm_accounting_lock_event_publication")
        allocation = source.find("nextval(")
        insertion = source.index("INSERT INTO deltallm_accounting_events")
        assert fence < insertion, row["proname"]
        assert allocation < 0 or fence < allocation, row["proname"]
        if row["proname"] == "deltallm_accounting_materialize_terminal_journal":
            assert source.index("FOR KEY SHARE") < fence


@pytest.mark.parametrize("planner", ["auto", "generic", "custom", "alternate_join"])
async def test_journal_parent_probes_do_not_scan_retained_window_history(accounting_db, planner):
    clients, generation = accounting_db
    db = clients[0]
    await db.execute_raw(
        "INSERT INTO deltallm_accounting_budget_windows(window_id,protocol_name,generation,"
        "scope_type,scope_id,period_key,policy_generation,limit_exact,window_starts_at,window_ends_at) "
        "SELECT $1::text||':parent-history:'||n,'primary',$1,'organization',"
        "$1::text||':parent-history:'||n,'history',1,0,NOW()-INTERVAL '2 days',"
        "NOW()-INTERVAL '1 day' FROM generate_series(1,10000) n",
        generation,
    )
    await db.execute_raw("ANALYZE deltallm_accounting_budget_windows")
    await pending(db, generation, outcome=AccountingOutcome.UNCERTAIN)
    async with capture_accounting_plans(os.environ["DATABASE_URL"], planner=planner) as captured:
        journal = AccountingJournalWorkerRepository(captured, statement_budget_seconds=2)
        claim = await journal.claim(
            generation=generation, worker_id="parent-plan", expires_at=deadline()
        )
        assert await journal.materialize(claim, expires_at=deadline()) == 4
    observed = False
    assert captured.errors == []
    for plan in captured.plans:
        if "FOR KEY SHARE" not in plan.query:
            continue
        for node in nodes(plan.node):
            if (
                node.get("Relation Name") != "deltallm_accounting_budget_windows"
                or not node["Actual Loops"]
            ):
                continue
            observed = True
            assert node["Node Type"] != "Seq Scan", plan.safe_report()
            assert node["Actual Rows"] <= 4, plan.safe_report()
            assert node["Actual Loops"] <= 4, plan.safe_report()
            assert node.get("Rows Removed by Filter", 0) <= 4, plan.safe_report()
    assert observed


@pytest.mark.parametrize("parts", [None, [-1], [64], [None], [0] * 257])
async def test_publication_rejects_unbounded_or_invalid_lock_pages(accounting_db, parts):
    clients, generation = accounting_db
    with pytest.raises(RawQueryError, match="accounting_event_publication_shape"):
        await clients[0].query_raw(
            "SELECT deltallm_accounting_lock_event_publication($1,$2::integer[])",
            generation,
            parts,
        )
