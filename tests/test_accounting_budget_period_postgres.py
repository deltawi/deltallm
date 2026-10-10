"""Recurring budgets renew and policy edits keep only current-period charges."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from uuid import uuid4

import pytest
from prisma.errors import RawQueryError

from src.billing.accounting_protocol import AccountingScope, ReserveDecision
from src.billing.spend import SpendTrackingService
from src.db.accounting_budget_reads import AccountingBudgetReadRepository
from src.billing.accounting_recovery import RecoveryAction
from src.db.accounting_recovery import AccountingRecoveryRepository
from src.db.accounting_read_model import AccountingReadModelRepository
from tests.test_accounting_budget_policy_history_postgres import _POLICIES, complete, owners
from tests.test_accounting_budget_regressions_postgres import issuer
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _reservation,
    accounting_db as _accounting_db,
)
from tests.test_accounting_recovery_postgres import recovery
from tests.test_preissued_permit_bank import fresh

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db
SCOPES = [scope for scope in _POLICIES if scope != "team_model"]


async def expire_policy(db, generation, scope, identity, *, roll=True):
    table, column, _ = _POLICIES[scope]
    # Simulate clock passage on this test row without firing policy-edit triggers.
    async with db.tx() as tx:
        await tx.execute_raw("SET LOCAL session_replication_role='replica'")
        await tx.execute_raw(
            f"UPDATE {table} SET budget_reset_at=NOW()-INTERVAL '30 minutes' WHERE {column}=$1",
            identity,
        )
    await db.execute_raw(
        "UPDATE deltallm_accounting_budget_windows SET window_starts_at=NOW()-INTERVAL '90 minutes',"
        "window_ends_at=NOW()-INTERVAL '30 minutes' WHERE generation=$1 AND scope_type=$2 AND scope_id=$3",
        generation,
        scope,
        identity,
    )
    if roll:
        worker, _ = recovery(db, generation)
        assert await worker.run_once(expires_at=deadline()) == 1


async def cleanup(db, item):
    for scope in SCOPES:
        table, column, attribute = _POLICIES[scope]
        await db.execute_raw(
            f"DELETE FROM {table} WHERE {column}=$1", getattr(item.attribution, attribute)
        )


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("roll", [False, True])
async def test_expired_reset_does_not_block_budget_or_metadata_edits(accounting_db, scope, roll):
    clients, generation = accounting_db
    db = clients[0]
    item = await owners(db, generation, None)
    table, column, attribute = _POLICIES[scope]
    identity = getattr(item.attribution, attribute)
    try:
        await db.execute_raw(
            f"UPDATE {table} SET max_budget=10,spend=0.2,spend_exact=0.2,budget_duration='1h',"
            f"budget_reset_at=NOW()+INTERVAL '30 minutes' WHERE {column}=$1",
            identity,
        )
        await expire_policy(db, generation, scope, identity, roll=roll)
        for edit in ("max_budget=11", 'metadata=\'{"name":"edited"}\'::jsonb'):
            await db.execute_raw(f"UPDATE {table} SET {edit} WHERE {column}=$1", identity)
        rows = await db.query_raw(
            f"SELECT spend_exact::text AS spend,budget_reset_at>NOW() AS future,metadata "
            f"FROM {table} WHERE {column}=$1",
            identity,
        )
        assert Decimal(rows[0]["spend"]) == 0 and rows[0]["future"]
        assert rows[0]["metadata"] == {"name": "edited"}
        balances = await AccountingBudgetReadRepository(db).balances(
            AccountingScope(scope), [identity]
        )
        assert balances[identity].spend == 0
        denied = fresh(item).model_copy(update={"allowance": Decimal(12)})
        assert (
            await issuer(db, generation).reserve_batch([denied], expires_at=deadline())
        ).permits[0].decision is ReserveDecision.BUDGET_EXHAUSTED
    finally:
        await cleanup(db, item)


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("projected", [False, True])
async def test_cap_toggle_after_reset_keeps_current_charge_once(accounting_db, scope, projected):
    clients, generation = accounting_db
    db = clients[0]
    item = await owners(db, generation, None)
    table, column, attribute = _POLICIES[scope]
    identity = getattr(item.attribution, attribute)
    terminal = None
    old_terminal = None
    try:
        await db.execute_raw(
            f"UPDATE {table} SET max_budget=10,spend=0.2,spend_exact=0.2,budget_duration='1h',"
            f"budget_reset_at=NOW()+INTERVAL '30 minutes' WHERE {column}=$1",
            identity,
        )
        old_terminal = await complete(
            db, generation, item, start=datetime.now(UTC) - timedelta(hours=2)
        )
        await expire_policy(db, generation, scope, identity)
        item = fresh(item)
        terminal = await complete(db, generation, item)
        if projected:
            await SpendTrackingService(db).log_batch_once(
                [
                    (str(event.event_id), "spend", event.spend_payload)
                    for event in (old_terminal, terminal)
                ]
            )
        reports = AccountingReadModelRepository(db, statement_budget_seconds=2)
        await reports.initialize(generation=generation, expires_at=deadline())
        for _ in range(4):
            page = await reports.claim(
                generation=generation,
                worker_id="period-report",
                limit=256,
                lease_seconds=30,
                expires_at=deadline(),
            )
            if page is not None:
                await reports.materialize(page, expires_at=deadline())
        for _ in range(2):
            await db.execute_raw(f"UPDATE {table} SET max_budget=NULL WHERE {column}=$1", identity)
            unlimited = (
                await AccountingBudgetReadRepository(db).balances(
                    AccountingScope(scope), [identity]
                )
            )[identity]
            assert unlimited.spend == Decimal("0.6")
            await db.execute_raw(f"UPDATE {table} SET max_budget=0.7 WHERE {column}=$1", identity)
            balances = await AccountingBudgetReadRepository(db).balances(
                AccountingScope(scope), [identity]
            )
            assert balances[identity].spend == Decimal("0.6")
            assert balances[identity].reset_at > datetime.now(UTC)
            request = fresh(item).model_copy(update={"allowance": Decimal("0.2")})
            assert (
                await issuer(db, generation).reserve_batch([request], expires_at=deadline())
            ).permits[0].decision is ReserveDecision.BUDGET_EXHAUSTED
        await db.execute_raw(f"UPDATE {table} SET max_budget=NULL WHERE {column}=$1", identity)
        async with db.tx() as tx:
            await tx.execute_raw("SET LOCAL session_replication_role='replica'")
            await tx.execute_raw(
                f"UPDATE {table} SET budget_reset_at=NOW()-INTERVAL '30 minutes' WHERE {column}=$1",
                identity,
            )
        expired = (
            await AccountingBudgetReadRepository(db).balances(AccountingScope(scope), [identity])
        )[identity]
        assert expired.spend == Decimal("0.6")
    finally:
        for event in (old_terminal, terminal):
            if event is not None:
                await db.execute_raw(
                    "DELETE FROM deltallm_spendlog_events WHERE id=$1", str(event.event_id)
                )
        await cleanup(db, item)


async def test_explicit_past_reset_is_rejected_without_changing_money(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    item = await owners(db, generation, None)
    org = item.attribution.organization_id
    try:
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET max_budget=10,spend=0.2,spend_exact=0.2,"
            "budget_duration='1h',budget_reset_at=NOW()+INTERVAL '30 minutes' WHERE organization_id=$1",
            org,
        )
        await expire_policy(db, generation, "organization", org)
        with pytest.raises(RawQueryError, match="accounting_budget_policy_renewal"):
            await db.execute_raw(
                "UPDATE deltallm_organizationtable SET budget_reset_at=NOW()-INTERVAL '5 minutes' "
                "WHERE organization_id=$1",
                org,
            )
        row = (
            await db.query_raw(
                "SELECT spend_exact::text AS spend,budget_reset_at<NOW() AS expired "
                "FROM deltallm_organizationtable WHERE organization_id=$1",
                org,
            )
        )[0]
        assert Decimal(row["spend"]) == Decimal("0.2") and row["expired"]
    finally:
        await cleanup(db, item)


@pytest.mark.parametrize("roll", [False, True])
async def test_removed_cap_cannot_return_on_a_later_reset(accounting_db, roll):
    clients, generation = accounting_db
    db = clients[0]
    item = await owners(db, generation, None)
    org = item.attribution.organization_id
    try:
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET max_budget=10,spend=0.2,spend_exact=0.2,"
            "budget_duration='1h',budget_reset_at=NOW()+INTERVAL '30 minutes' WHERE organization_id=$1",
            org,
        )
        await expire_policy(db, generation, "organization", org, roll=roll)
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET max_budget=NULL WHERE organization_id=$1", org
        )
        worker, _ = recovery(db, generation)
        assert await worker.run_once(expires_at=deadline()) == 0
        assert await db.query_raw(
            "SELECT count(*)::int AS count FROM deltallm_accounting_budget_windows "
            "WHERE generation=$1 AND scope_id=$2 AND renewal_spec IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM deltallm_accounting_budget_windows n "
            "WHERE n.generation=$1 AND n.scope_id=$2 AND n.window_starts_at>=deltallm_accounting_budget_windows.window_ends_at)",
            generation,
            org,
        ) == [{"count": 0}]
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET max_budget=0.1 WHERE organization_id=$1", org
        )
        balance = (
            await AccountingBudgetReadRepository(db).balances(AccountingScope.ORGANIZATION, [org])
        )[org]
        assert balance.spend == 0
    finally:
        await cleanup(db, item)


async def test_rollover_waits_for_policy_edit_and_does_not_restore_removed_cap(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    item = await owners(db, generation, None)
    org = item.attribution.organization_id
    task = None
    try:
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET max_budget=10,budget_duration='1h',"
            "budget_reset_at=NOW()+INTERVAL '30 minutes' WHERE organization_id=$1",
            org,
        )
        await expire_policy(db, generation, "organization", org, roll=False)
        async with db.tx() as tx:
            await tx.query_raw(
                "SELECT generation FROM deltallm_accounting_protocols WHERE generation=$1 FOR UPDATE",
                generation,
            )
            task = asyncio.create_task(
                AccountingRecoveryRepository(clients[1], statement_budget_seconds=2).recover(
                    RecoveryAction.ROLL_WINDOWS,
                    generation=generation,
                    limit=4,
                    expires_at=deadline(),
                )
            )
            async with asyncio.timeout(1):
                while True:
                    waiting = await tx.query_raw(
                        "SELECT count(*)::int AS count FROM pg_stat_activity "
                        "WHERE pid<>pg_backend_pid() AND wait_event='transactionid' "
                        "AND query LIKE '%SELECT deltallm_accounting_roll_windows(%'"
                    )
                    if waiting[0]["count"]:
                        break
                    if task.done():
                        pytest.fail("rollover did not wait for the policy fence")
            await tx.execute_raw(
                "UPDATE deltallm_organizationtable SET max_budget=NULL WHERE organization_id=$1",
                org,
            )
        assert await task == 0
    finally:
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await cleanup(db, item)


async def test_legacy_mode_keeps_its_existing_reset_owner(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    item = await owners(db, generation, None)
    org = item.attribution.organization_id
    try:
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET max_budget=10,spend=0.2,spend_exact=0.2,"
            "budget_duration='1h',budget_reset_at=NOW()+INTERVAL '30 minutes' WHERE organization_id=$1",
            org,
        )
        await expire_policy(db, generation, "organization", org, roll=False)
        await db.execute_raw(
            "UPDATE deltallm_accounting_protocols SET state='fenced' WHERE generation=$1",
            generation,
        )
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET metadata='{}'::jsonb WHERE organization_id=$1",
            org,
        )
        row = (
            await db.query_raw(
                "SELECT spend_exact::text AS spend,budget_reset_at<NOW() AS expired FROM deltallm_organizationtable "
                "WHERE organization_id=$1",
                org,
            )
        )[0]
        assert Decimal(row["spend"]) == Decimal("0.2") and row["expired"]
    finally:
        await cleanup(db, item)


@pytest.mark.parametrize("settings", [None, "old", []])
async def test_monthly_edit_keeps_anchor_when_reset_metadata_is_not_an_object(
    accounting_db, settings
):
    clients, generation = accounting_db
    db = clients[0]
    item = await owners(db, generation, None)
    org = item.attribution.organization_id
    now = datetime.now(UTC)
    next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
    reset = (next_month - timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=0)
    try:
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET max_budget=10,budget_duration='1mo',"
            'budget_reset_at=$2::timestamp,metadata=\'{"_budget_reset":{"monthly_anchor_day":31}}\'::jsonb '
            "WHERE organization_id=$1",
            org,
            reset.replace(tzinfo=None).isoformat(),
        )
        async with db.tx() as tx:
            await tx.execute_raw("SET LOCAL session_replication_role='replica'")
            await tx.execute_raw(
                "UPDATE deltallm_organizationtable SET budget_reset_at='2026-01-31 23:59:59' WHERE organization_id=$1",
                org,
            )
        await db.execute_raw(
            "UPDATE deltallm_organizationtable SET metadata=$2::jsonb WHERE organization_id=$1",
            org,
            json.dumps({"_budget_reset": settings, "keep": "operator metadata"}),
        )
        row = (
            await db.query_raw(
                "SELECT metadata FROM deltallm_organizationtable WHERE organization_id=$1",
                org,
            )
        )[0]
        assert row["metadata"] == {
            "_budget_reset": {"monthly_anchor_day": 31},
            "keep": "operator metadata",
        }
    finally:
        await cleanup(db, item)


@pytest.mark.parametrize("spec", ["1h", "1d", "1mo"])
async def test_native_recovery_renews_once_and_admission_uses_new_window(accounting_db, spec):
    clients, generation = accounting_db
    window = str(uuid4())
    await _create_window(clients[0], generation, window)
    await clients[0].execute_raw(
        "UPDATE deltallm_accounting_budget_windows SET window_starts_at=NOW()-INTERVAL '2 days',"
        "window_ends_at=NOW()-INTERVAL '1 minute',renewal_spec=$2,renewal_anchor_day=$3,"
        "committed_exact=1.5,provisional_exact=0.2 WHERE window_id=$1",
        window,
        spec,
        datetime.now(UTC).day if spec == "1mo" else None,
    )
    workers = [recovery(db, generation)[0] for db in clients]
    counts = await asyncio.gather(*(worker.run_once(expires_at=deadline()) for worker in workers))
    assert sum(counts) == 1
    assert await workers[0].run_once(expires_at=deadline()) == 0
    old = (
        await clients[0].query_raw(
            "SELECT committed_exact::text AS spent,provisional_exact::text AS held "
            "FROM deltallm_accounting_budget_windows WHERE window_id=$1",
            window,
        )
    )[0]
    assert Decimal(old["spent"]) == Decimal("1.5") and Decimal(old["held"]) == Decimal("0.2")
    permit = (
        await issuer(clients[0], generation).reserve_batch(
            [_reservation(generation, window, explicit_window=False)],
            expires_at=deadline(),
        )
    ).permits[0]
    assert permit.decision is ReserveDecision.DISPATCH


@pytest.mark.parametrize(
    "now,expected",
    [
        ("2024-02-01 00:00:00+00", "2024-02-29 12:00:00+00"),
        ("2025-02-01 00:00:00+00", "2025-02-28 12:00:00+00"),
        ("2025-03-01 00:00:00+00", "2025-03-31 12:00:00+00"),
    ],
)
async def test_monthly_renewal_keeps_utc_anchor_under_other_session_timezone(
    accounting_db, now, expected
):
    clients, _ = accounting_db
    async with clients[0].tx() as tx:
        await tx.execute_raw("SET LOCAL timezone='America/New_York'")
        rows = await tx.query_raw(
            "SELECT deltallm_accounting_next_window_end('1mo','2024-01-31 12:00:00+00',31,$1::timestamptz) "
            "=$2::timestamptz AS correct",
            now,
            expected,
        )
        assert rows == [{"correct": True}]
