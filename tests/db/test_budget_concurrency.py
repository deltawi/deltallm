from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.budget import BudgetEnforcementService, BudgetExceeded, BudgetStateUnavailable
from src.billing.ledger import SpendLedgerService
from src.db.budget_notifications import BudgetNotificationRepository
from src.db.budget_reconciliation import BudgetCounterChanged, BudgetReconciliationRepository
from tests.test_telemetry_ingestion_db_integration import _connect_prisma

pytestmark = pytest.mark.postgres


@pytest.fixture
async def budget_db():
    db = await _connect_prisma()
    identity = str(uuid4())
    try:
        await db.execute_raw(
            """
            WITH org AS (
                INSERT INTO deltallm_organizationtable (id, organization_id, max_budget, spend)
                VALUES ($1, $1, 10, 1) RETURNING organization_id
            ), team AS (
                INSERT INTO deltallm_teamtable (team_id, organization_id, models, max_budget, spend, model_max_budget)
                SELECT $1, organization_id, ARRAY[]::text[], 10, 1, '{"model":10}'::jsonb FROM org RETURNING team_id
            ), principal AS (
                INSERT INTO deltallm_usertable (user_id, team_id, models, max_budget, spend)
                SELECT $1, team_id, ARRAY[]::text[], 10, 1 FROM team RETURNING user_id, team_id
            )
            INSERT INTO deltallm_verificationtoken (id, token, user_id, team_id, models, max_budget, spend)
            SELECT $1, $1, user_id, team_id, ARRAY[]::text[], 10, 1 FROM principal
            """,
            identity,
        )
        await db.execute_raw(
            """INSERT INTO deltallm_teammodelspend (team_id, model, spend, updated_at, reconciled_at)
               VALUES ($1, 'model', 1, NOW(), NOW())""",
            identity,
        )
        yield db, identity
    finally:
        for table, column in (
            ("deltallm_teammodelspend", "team_id"),
            ("deltallm_verificationtoken", "token"),
            ("deltallm_usertable", "user_id"),
            ("deltallm_teamtable", "team_id"),
            ("deltallm_organizationtable", "organization_id"),
        ):
            await db.execute_raw(f"DELETE FROM {table} WHERE {column}=$1", identity)
        await db.disconnect()


def scopes(identity):
    return dict(
        api_key=identity,
        user_id=identity,
        team_id=identity,
        organization_id=identity,
        model="model",
    )


class CountingDB:
    def __init__(self, db):
        self.db = db
        self.calls = []

    async def query_raw(self, query, *args):
        self.calls.append(query)
        return await self.db.query_raw(query, *args)

    async def execute_raw(self, query, *args):
        self.calls.append(query)
        return await self.db.execute_raw(query, *args)


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [None, "key", "user", "team", "org", "team_model"])
async def test_real_combined_parity_all_scopes(budget_db, denied):
    db, identity = budget_db
    tables = {
        "key": ("deltallm_verificationtoken", "token"),
        "user": ("deltallm_usertable", "user_id"),
        "team": ("deltallm_teamtable", "team_id"),
        "org": ("deltallm_organizationtable", "organization_id"),
        "team_model": ("deltallm_teammodelspend", "team_id"),
    }
    if denied:
        table, column = tables[denied]
        await db.execute_raw(
            f"UPDATE {table} SET spend=10, spend_exact=10 WHERE {column}=$1", identity
        )
    results = []
    counts = []
    for mode in ("legacy", "combined"):
        counted = CountingDB(db)
        try:
            await BudgetEnforcementService(counted, query_mode=mode).check_budgets(
                **scopes(identity)
            )
            results.append(None)
        except BudgetExceeded as exc:
            results.append(exc.entity_type)
        counts.append(len(counted.calls))
    assert results == [denied, denied]
    assert counts[1] == 1
    if denied is None:
        assert counts[0] == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "combined"])
async def test_partial_recreated_rollup_stays_unavailable_until_explicit_repair(budget_db, mode):
    db, identity = budget_db
    await db.execute_raw("DELETE FROM deltallm_teammodelspend WHERE team_id=$1", identity)
    service = BudgetEnforcementService(db, query_mode=mode)
    with pytest.raises(BudgetStateUnavailable):
        await service.check_budgets(**scopes(identity))
    await SpendLedgerService(db, strict=True)._increment_team_model_counter(
        team_id=identity, model="model", amount=Decimal(1)
    )
    with pytest.raises(BudgetStateUnavailable):
        await service.check_budgets(**scopes(identity))
    row = (
        await db.query_raw(
            "SELECT spend, updated_at FROM deltallm_teammodelspend WHERE team_id=$1", identity
        )
    )[0]
    await BudgetReconciliationRepository(db).repair(
        team_id=identity,
        model="model",
        verified_total=Decimal(12),
        expected_spend=Decimal(1),
        expected_updated_at=row["updated_at"],
    )
    with pytest.raises(BudgetExceeded) as exc:
        await service.check_budgets(**scopes(identity))
    assert exc.value.entity_type == "team_model"
    with pytest.raises(BudgetCounterChanged):
        await BudgetReconciliationRepository(db).repair(
            team_id=identity,
            model="model",
            verified_total=Decimal(0),
            expected_spend=Decimal(1),
            expected_updated_at=row["updated_at"],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "combined"])
async def test_real_concurrent_overdue_resets_preserve_calendar_anchor(budget_db, mode):
    db, identity = budget_db
    await db.execute_raw(
        """UPDATE deltallm_organizationtable SET spend=12, spend_exact=12,
        budget_duration='1mo', budget_reset_at='2020-01-30', metadata='{}'::jsonb WHERE organization_id=$1""",
        identity,
    )
    service = BudgetEnforcementService(db, query_mode=mode)
    args = dict(api_key=None, user_id=None, team_id=None, organization_id=identity)
    await asyncio.gather(*(service.check_budgets(**args) for _ in range(8)))
    row = (
        await db.query_raw(
            "SELECT spend_exact::text, budget_reset_at, metadata FROM deltallm_organizationtable WHERE organization_id=$1",
            identity,
        )
    )[0]
    assert Decimal(row["spend_exact"]) == 0
    assert datetime.fromisoformat(row["budget_reset_at"].replace("Z", "+00:00")).replace(
        tzinfo=UTC
    ) > datetime.now(UTC)
    assert row["metadata"]["_budget_reset"]["monthly_anchor_day"] == 30


@pytest.mark.asyncio
async def test_real_notification_dedupe_and_fenced_recovery(budget_db):
    db, identity = budget_db
    repo = BudgetNotificationRepository(db)
    args = dict(
        organization_id=identity,
        spend=Decimal(8),
        soft_budget=Decimal(7),
        hard_budget=Decimal(10),
        ttl_seconds=60,
    )
    outcomes = await asyncio.gather(*(repo.enqueue(**args) for _ in range(12)))
    assert outcomes.count("queued") == 1
    assert set(outcomes) <= {"queued", "throttled", "busy"}
    original = await repo.claim()
    assert original.organization_id == identity
    assert await repo.claim() is None
    await db.execute_raw(
        "UPDATE deltallm_budgetnotification SET available_at=NOW()-INTERVAL '1 second', lease_expires_at=NOW()-INTERVAL '1 second' WHERE organization_id=$1",
        identity,
    )
    replacement = await repo.claim()
    assert replacement.claim_token != original.claim_token
    assert not await repo.begin_dispatch(original)
    assert await repo.begin_dispatch(replacement)
    await db.execute_raw(
        "UPDATE deltallm_budgetnotification SET available_at=NOW()-INTERVAL '1 second', lease_expires_at=NOW()-INTERVAL '1 second' WHERE organization_id=$1",
        identity,
    )
    recovered = await repo.claim()
    assert recovered.status == "failed"
    assert not await repo.begin_dispatch(replacement)
    row = (
        await db.query_raw(
            "SELECT outcome FROM deltallm_budgetnotification WHERE organization_id=$1", identity
        )
    )[0]
    assert row["outcome"] == "delivery_unknown"
    held = (
        await db.query_raw(
            "SELECT dedupe_until > NOW() + INTERVAL '6 days' AS held FROM deltallm_budgetnotification WHERE organization_id=$1",
            identity,
        )
    )[0]
    assert held["held"] is True
    assert await repo.enqueue(**args) == "throttled"


@pytest.mark.asyncio
async def test_real_notification_full_capacity_sheds_without_growing(budget_db):
    from datetime import timedelta

    db, identity = budget_db
    prefix = "pr5-capacity-" + uuid4().hex + "-"

    # Roll back the synthetic capacity fixture, including its organization rows.
    class RollbackFixture(Exception):
        pass

    with pytest.raises(RollbackFixture):
        async with db.tx(timeout=timedelta(seconds=30)) as tx:
            await tx.execute_raw(
                """WITH orgs AS (
                INSERT INTO deltallm_organizationtable (id,organization_id)
                SELECT $1 || n::text,$1 || n::text FROM generate_series(1,10000) n RETURNING organization_id)
                INSERT INTO deltallm_budgetnotification
                    (organization_id,notification_id,spend,soft_budget,dedupe_until)
                SELECT organization_id,organization_id,8,7,NOW()+INTERVAL '1 hour' FROM orgs""",
                prefix,
            )
            result = await BudgetNotificationRepository(tx).enqueue(
                organization_id=identity,
                spend=Decimal(8),
                soft_budget=Decimal(7),
                hard_budget=Decimal(10),
                ttl_seconds=60,
            )
            assert result == "full"
            assert not await tx.query_raw(
                "SELECT 1 FROM deltallm_budgetnotification WHERE organization_id=$1", identity
            )
            raise RollbackFixture()


@pytest.mark.asyncio
async def test_real_prompt_top_lookup_bounds_equal_priority_bindings(budget_db):
    from tests.performance.measure_prompt_fills import binding_plan
    from src.db.prompt_registry import PromptRegistryRepository

    db, identity = budget_db
    try:
        await db.execute_raw(
            "INSERT INTO deltallm_prompttemplate (prompt_template_id,template_key,name,created_at,updated_at) VALUES ($1,$1,$1,NOW(),NOW())",
            identity,
        )
        await db.execute_raw(
            """INSERT INTO deltallm_promptbinding
            (prompt_binding_id,scope_type,scope_id,prompt_template_id,label,priority,created_at,updated_at)
            SELECT $1 || n::text,'organization',$1,$1,'label-' || n::text,100,NOW(),NOW()
            FROM generate_series(1,1000) n""",
            identity,
        )
        await db.execute_raw("ANALYZE deltallm_promptbinding")
        plan = await binding_plan(db, identity)

        def check(node):
            if node.get("Relation Name") == "deltallm_promptbinding":
                assert node["Actual Rows"] <= 1
            for child in node.get("Plans", []):
                check(child)

        check(plan["plan"])
        rows = await PromptRegistryRepository(db).resolve_binding_chain(
            scopes=[("organization", identity)]
        )
        assert len(rows) == 1
        assert rows[0].scope_type == "organization"
        assert rows[0].priority == 100
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_prompttemplate WHERE prompt_template_id=$1", identity
        )


@pytest.mark.asyncio
async def test_real_budget_precision_parity_below_threshold(budget_db):
    db, identity = budget_db
    await db.execute_raw(
        "UPDATE deltallm_verificationtoken SET max_budget=1, spend_exact=0.999999999999999999 WHERE token=$1",
        identity,
    )
    for mode in ("legacy", "combined"):
        await BudgetEnforcementService(db, query_mode=mode).check_budgets(**scopes(identity))


@pytest.mark.asyncio
async def test_combined_budget_uses_full_counter_key_for_hot_team(budget_db):
    from tests.performance.measure_budget_dependencies import TABLES, query_plan, seed

    db, identity = budget_db
    prefix = identity + "-cardinality-"
    try:
        # Both entity tables and the hot team's model fanout need representative
        # cardinality; a one-row entity table legitimately uses a sequential scan.
        await seed(db, prefix, 5000)
        await query_plan(db, {**scopes(prefix + "1"), "model": "other-model-10000"})
    finally:
        for table, column in TABLES:
            await db.execute_raw(f"DELETE FROM {table} WHERE {column} LIKE $1", prefix + "%")
