"""Routing reports seek the native receipt identity, not the legacy operation id."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.billing.routing_costs import aggregate_routing_costs
from src.db.routing_costs import routing_cost_observation, routing_cost_query
from src.services.spend_visibility import SpendVisibility
from tests.test_accounting_protocol_postgres import accounting_db as _accounting_db
from tests.test_accounting_reporting_parity_postgres import projected

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


def report_query(attribution, *, owner="native-report-owner"):
    end = datetime.now(UTC) + timedelta(minutes=1)
    return routing_cost_query(
        visibility=SpendVisibility(
            False,
            owner_account_id=owner,
            self_organization_ids=(attribution.organization_id,),
        ),
        start=end - timedelta(days=1),
        end=end,
        limit=10,
    )


async def test_native_answer_uses_accepted_sequence_and_preserves_tenant_denial(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    values = await projected(
        db,
        generation,
        count=2,
        owner_account_id="native-report-owner",
        billing={
            "billing_unit": "token",
            "usage_snapshot": {
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "total_tokens": 5,
            },
        },
    )
    attribution = values[0].receipt.reservation.attribution
    assert all(
        value.finalization.event_id != value.receipt.reservation.operation_id for value in values
    )
    query = report_query(attribution)
    rows = await db.query_raw(query.sql, *query.params)
    assert len(rows) == 2
    assert {row["operation_id"] for row in rows} == {
        str(value.receipt.reservation.operation_id) for value in values
    }
    report = aggregate_routing_costs(tuple(routing_cost_observation(row) for row in rows))
    assert Decimal(report.answer_provider_cost_exact) == Decimal("0.8")
    assert Decimal(report.answer_customer_charge_exact) == Decimal("1.2")
    assert all(row["total_tokens"] == 5 for row in rows)
    denied = report_query(attribution, owner="not-the-verified-owner")
    assert await db.query_raw(denied.sql, *denied.params) == []

    # A mismatched sequence must not borrow another operation's reported charge.
    first, second = [str(value.receipt.reservation.operation_id) for value in values]
    await db.execute_raw(
        "UPDATE deltallm_billing_operations SET final_event_sequence=("
        "SELECT final_event_sequence FROM deltallm_billing_operations WHERE operation_id=$2)"
        " WHERE operation_id=$1",
        first,
        second,
    )
    rows = await db.query_raw(query.sql, *query.params)
    invalid = next(row for row in rows if row["operation_id"] == first)
    assert invalid["answer_provider_cost"] is invalid["answer_customer_charge"] is None
    assert aggregate_routing_costs(tuple(routing_cost_observation(row) for row in rows)).partial


async def test_native_projection_lag_does_not_fall_back_to_colliding_legacy_charge(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    values = await projected(db, generation, count=1, owner_account_id="native-report-owner")
    value = values[0]
    attribution = value.receipt.reservation.attribution
    operation_id, event_id = (
        str(value.receipt.reservation.operation_id),
        str(value.finalization.event_id),
    )
    try:
        await db.execute_raw(
            "INSERT INTO deltallm_spendlog_events "
            "(id,request_id,call_type,api_key,model,spend,spend_exact,start_time,end_time,"
            "organization_id,owner_account_id,usage_snapshot) "
            "SELECT $1,request_id,call_type,api_key,model,999,999,start_time,end_time,"
            'organization_id,owner_account_id,\'{"prompt_tokens":3,"completion_tokens":2}\'::jsonb '
            "FROM deltallm_accounting_usage_facts_v2 WHERE event_id=$2",
            operation_id,
            event_id,
        )
        await db.execute_raw(
            "UPDATE deltallm_billing_operations SET final_event_sequence=NULL WHERE operation_id=$1",
            operation_id,
        )
        query = report_query(attribution)
        rows = await db.query_raw(query.sql, *query.params)
        assert len(rows) == 1
        assert rows[0]["answer_customer_charge"] is rows[0]["answer_provider_cost"] is None
        assert routing_cost_observation(rows[0]).answer_customer_charge is None
    finally:
        await db.execute_raw("DELETE FROM deltallm_spendlog_events WHERE id=$1", operation_id)
