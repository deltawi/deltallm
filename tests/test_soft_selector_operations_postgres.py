import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.operation_reservation import BillingOperationUnavailable, SoftSelectorOperation
from src.billing.spend import SpendTrackingService
from src.billing.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.db.billing_operation_recovery import BillingOperationRecovery
from src.db.billing_operations import BillingOperationRepository
from tests import test_billing_operations_postgres as operation_fixtures
from tests.test_billing_operations_postgres import deadline, hold

operation_db = operation_fixtures.operation_db
selector_billing_db = operation_fixtures.selector_billing_db

pytestmark = pytest.mark.postgres


async def test_soft_admission_query_plan_uses_one_counter_and_never_reads_history(operation_db):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from src.db.soft_selector_admission import check_soft_selector_admission

    db, _, charge = operation_db
    operation = soft_operation(charge)
    await db.execute_raw(
        "UPDATE deltallm_teamtable SET model_max_budget=jsonb_build_object($2::text,100) WHERE team_id=$1",
        operation.attribution.team_id,
        operation.attribution.model_group,
    )
    await db.execute_raw(
        "INSERT INTO deltallm_teammodelspend(team_id,model,spend,spend_exact,updated_at) "
        "SELECT $1,'plan-fixture-'||n,0,0,NOW() FROM generate_series(1,10000) n",
        operation.attribution.team_id,
    )
    await db.execute_raw("ANALYZE deltallm_teammodelspend")
    captured = AsyncMock(return_value=[{}])
    await check_soft_selector_admission(SimpleNamespace(query_raw=captured), operation)
    captured.assert_awaited_once()
    sql, *parameters = captured.call_args.args
    rows = await db.query_raw("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, *parameters)
    report = rows[0]["QUERY PLAN"][0]
    pending, nodes = [report["Plan"]], []
    while pending:
        node = pending.pop()
        nodes.append(
            {
                key: node[key]
                for key in (
                    "Node Type",
                    "Relation Name",
                    "Index Name",
                    "Actual Rows",
                    "Actual Loops",
                )
                if key in node
            }
        )
        pending.extend(node.get("Plans", []))
    assert report["Plan"]["Actual Rows"] == 1
    assert "deltallm_spendlog_events" not in sql and "SUM(" not in sql.upper()
    counter_nodes = [
        node for node in nodes if node.get("Relation Name") == "deltallm_teammodelspend"
    ]
    assert len(counter_nodes) == 1
    assert counter_nodes[0]["Node Type"] == "Index Scan"
    assert counter_nodes[0]["Actual Rows"] == counter_nodes[0]["Actual Loops"] == 1
    print(
        json.dumps(
            {
                "execution_ms": report["Execution Time"],
                "planning_ms": report["Planning Time"],
                "nodes": nodes,
            }
        )
    )


def soft_operation(charge):
    return SoftSelectorOperation(
        attribution=charge.attribution,
        owner_token=uuid4(),
        pricing=charge.pricing,
        admission_allowance=charge.customer_charge * 10,
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )


@pytest.mark.parametrize(
    "table,column,scope",
    [
        ("deltallm_verificationtoken", "token", "api_key"),
        ("deltallm_usertable", "user_id", "user_id"),
        ("deltallm_teamtable", "team_id", "team_id"),
        ("deltallm_organizationtable", "organization_id", "organization_id"),
    ],
)
async def test_soft_selector_admission_includes_allowance_in_each_budget_scope(
    operation_db, table, column, scope
):
    db, _, charge = operation_db
    operation = soft_operation(charge)
    scope_id = getattr(operation.attribution, scope)
    await db.execute_raw(
        f"UPDATE {table} SET max_budget=$2::numeric::double precision WHERE {column}=$1",
        scope_id,
        str(operation.admission_allowance / 2),
    )
    with pytest.raises(BillingOperationUnavailable):
        await BillingOperationRepository(db).reserve(operation, expires_at=deadline())
    rows = await db.query_raw(
        "SELECT operation_id FROM deltallm_billing_operations WHERE operation_id=$1",
        str(operation.attribution.operation_id),
    )
    assert not rows and await hold(db, operation) == 0


async def test_soft_actual_selector_charge_can_exceed_admission_estimate(operation_db):
    db, _, charge = operation_db
    operation = soft_operation(charge).model_copy(
        update={"admission_allowance": charge.customer_charge / 2}
    )
    store = BillingOperationRepository(db)
    await store.reserve(operation, expires_at=deadline())
    await store.dispatch(operation, component="selector", expires_at=deadline())
    await store.accept_selector(operation, charge, expires_at=deadline())
    row = (
        await db.query_raw(
            "SELECT selector_state FROM deltallm_billing_operations WHERE operation_id=$1",
            str(operation.attribution.operation_id),
        )
    )[0]
    assert row["selector_state"] == "accepted"


async def test_soft_selector_receipt_settles_once_without_holds_or_answer_reservation(operation_db):
    db, _, charge = operation_db
    operation = soft_operation(charge)
    store = BillingOperationRepository(db)
    admitted = await store.reserve(operation, expires_at=deadline())
    assert admitted.answer_state == "unattempted"
    assert await hold(db, operation) == 0
    await store.dispatch(operation, component="selector", expires_at=deadline())
    await store.accept_selector(operation, charge, expires_at=deadline())
    recovery = BillingOperationRecovery(
        store, max_pending_events=100000, max_attempts=10, selector_events_only=True
    )
    service = SpendIngestionService(
        db_client=db,
        writer=SpendTrackingService(db),
        operation_recovery=recovery,
        config=SpendIngestionConfig(enabled=True, worker_enabled=False),
    )
    await service._process_batch(await service._claim_batch())
    await store.accept_selector(operation, charge, expires_at=deadline())
    assert await service._claim_batch() == []
    row = (
        await db.query_raw(
            "SELECT selector_state,closed_at,selector_allowance::text AS allowance FROM deltallm_billing_operations WHERE operation_id=$1",
            str(operation.attribution.operation_id),
        )
    )[0]
    assert row["selector_state"] == "settled" and row["closed_at"] is not None
    assert Decimal(row["allowance"]) == 0
    spend = (
        await db.query_raw(
            "SELECT spend_exact::text AS amount FROM deltallm_verificationtoken WHERE token=$1",
            operation.attribution.api_key,
        )
    )[0]
    assert Decimal(spend["amount"]) == charge.customer_charge


async def test_soft_admission_allows_concurrent_overshoot_without_reserving_accounts(operation_db):
    db, _, charge = operation_db
    operation = soft_operation(charge)
    await db.execute_raw(
        "UPDATE deltallm_verificationtoken SET max_budget=$2::numeric::double precision WHERE token=$1",
        operation.attribution.api_key,
        str(operation.admission_allowance * 2),
    )
    store = BillingOperationRepository(db)
    operations = [
        operation.model_copy(
            update={
                "attribution": operation.attribution.model_copy(update={"operation_id": uuid4()}),
                "owner_token": uuid4(),
            }
        )
        for _ in range(4)
    ]
    admitted = await asyncio.gather(
        *(store.reserve(candidate, expires_at=deadline()) for candidate in operations)
    )
    assert len(admitted) == 4 and await hold(db, operation) == 0
    for candidate in operations:
        await store.unattempted(candidate, component="selector", expires_at=deadline())


async def test_soft_unknown_dispatch_remains_pending_without_replaying_or_inventing_free_usage(
    operation_db,
):
    db, _, charge = operation_db
    operation = soft_operation(charge)
    store = BillingOperationRepository(db)
    await store.reserve(operation, expires_at=deadline())
    await store.dispatch(operation, component="selector", expires_at=deadline())
    await db.execute_raw(
        "UPDATE deltallm_billing_operations SET created_at=NOW()-interval '10 minutes',expires_at=NOW()-interval '5 minutes' WHERE operation_id=$1",
        str(operation.attribution.operation_id),
    )
    await BillingOperationRecovery(store, max_pending_events=100000, max_attempts=10).recover()
    row = (
        await db.query_raw(
            "SELECT selector_state,selector_receipt,closed_at FROM deltallm_billing_operations WHERE operation_id=$1",
            str(operation.attribution.operation_id),
        )
    )[0]
    assert row == {"selector_state": "pending", "selector_receipt": None, "closed_at": None}
    with pytest.raises(BillingOperationUnavailable):
        await store.dispatch(operation, component="selector", expires_at=deadline())


async def test_soft_selector_does_not_require_a_preexisting_team_model_counter(operation_db):
    db, _, charge = operation_db
    await db.execute_raw(
        "DELETE FROM deltallm_teammodelspend WHERE team_id=$1 AND model=$2",
        charge.attribution.team_id,
        charge.attribution.model_group,
    )
    operation = soft_operation(charge)
    store = BillingOperationRepository(db)
    await store.reserve(operation, expires_at=deadline())
    await store.unattempted(operation, component="selector", expires_at=deadline())
    assert await hold(db, operation) == 0


@pytest.mark.parametrize("missing_counter", [False, True])
async def test_soft_selector_team_model_budget_denies_exhausted_or_missing_counter(
    operation_db, missing_counter
):
    db, _, charge = operation_db
    operation = soft_operation(charge)
    await db.execute_raw(
        "UPDATE deltallm_teamtable SET model_max_budget=jsonb_build_object($2::text,$3::numeric) WHERE team_id=$1",
        charge.attribution.team_id,
        charge.attribution.model_group,
        str(operation.admission_allowance / 2),
    )
    if missing_counter:
        await db.execute_raw(
            "DELETE FROM deltallm_teammodelspend WHERE team_id=$1 AND model=$2",
            charge.attribution.team_id,
            charge.attribution.model_group,
        )
        await db.execute_raw(
            "UPDATE deltallm_teamtable SET model_max_budget=jsonb_build_object($2::text,100) WHERE team_id=$1",
            charge.attribution.team_id,
            charge.attribution.model_group,
        )
    with pytest.raises(BillingOperationUnavailable):
        await BillingOperationRepository(db).reserve(operation, expires_at=deadline())
    assert await hold(db, operation) == 0
    assert not await db.query_raw(
        "SELECT operation_id FROM deltallm_billing_operations WHERE operation_id=$1",
        str(operation.attribution.operation_id),
    )


@pytest.mark.parametrize(
    "field", ["api_key", "user_id", "team_id", "organization_id", "owner_account_id"]
)
async def test_soft_journal_rechecks_durable_attribution_before_admission(operation_db, field):
    db, _, charge = operation_db
    operation = soft_operation(charge)
    operation = operation.model_copy(
        update={"attribution": operation.attribution.model_copy(update={field: "unrelated-scope"})}
    )
    with pytest.raises(BillingOperationUnavailable):
        await BillingOperationRepository(db).reserve(operation, expires_at=deadline())
    assert (
        await db.query_raw(
            "SELECT operation_id FROM deltallm_billing_operations WHERE operation_id=$1",
            str(operation.attribution.operation_id),
        )
        == []
    )
