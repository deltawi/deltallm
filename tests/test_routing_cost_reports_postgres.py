from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json

import pytest

from src.billing.operation_reservation import ComponentState
from src.billing.spend import SpendTrackingService
from src.db.billing_operations import BillingOperationRepository
from src.db.routing_costs import routing_cost_observation, routing_cost_query
from src.services.spend_visibility import SpendVisibility
from tests import test_billing_operations_postgres as operation_fixtures
from tests.test_billing_operations_postgres import deadline
from tests.test_soft_selector_operations_postgres import soft_operation

selector_billing_db = operation_fixtures.selector_billing_db
operation_db = operation_fixtures.operation_db
pytestmark = pytest.mark.postgres


def query_for(charge, *, visibility=None, before=None, limit=100):
    end = datetime.now(UTC) + timedelta(seconds=1)
    return routing_cost_query(
        visibility=visibility
        or SpendVisibility(False, organization_ids=(charge.attribution.organization_id,)),
        start=end - timedelta(days=2),
        end=end,
        before=before,
        limit=limit,
        model_group=charge.attribution.model_group,
    )


async def reserve(db, charge):
    operation = soft_operation(charge)
    store = BillingOperationRepository(db)
    await store.reserve(operation, expires_at=deadline())
    await store.dispatch(operation, component="selector", expires_at=deadline())
    await store.accept_selector(operation, charge, expires_at=deadline())
    return operation


async def write_answer(db, charge, *, reported=True, unpriced=False, failed=False):
    payload = charge.spend_payload()
    payload.update(
        call_type="chat_completion", cost="0.04", cost_exact="0.04", provider_cost_exact="0.02"
    )
    payload["metadata"] = {
        "deployment_model": "answer-model",
        "provider": "openai",
        "provider_cost": "0.02",
        "billing": {
            "billing_unit": "token",
            "usage_snapshot": {"prompt_tokens": 10, "completion_tokens": 5}
            if reported
            else {"kind": "unknown"},
            **({"unpriced_reason": "missing_price"} if unpriced else {}),
        },
    }
    if failed:
        payload["status"] = "error"
    await SpendTrackingService(db).log_batch_once(
        [(str(charge.attribution.operation_id), "spend", payload)]
    )


async def observations(db, charge, **kwargs):
    query = query_for(charge, **kwargs)
    rows = await db.query_raw(query.sql, *query.params)
    return rows, [routing_cost_observation(row) for row in rows]


async def test_real_soft_journal_answer_is_not_free_and_reporting_does_not_mutate_ledger(
    operation_db,
):
    db, _, charge = operation_db
    await reserve(db, charge)
    await write_answer(db, charge)
    before = await db.query_raw(
        "SELECT spend_exact::text AS spend FROM deltallm_verificationtoken WHERE token=$1",
        charge.attribution.api_key,
    )
    _, rows = await observations(db, charge)
    assert len(rows) == 1
    assert rows[0].answer_state is ComponentState.SETTLED
    assert rows[0].answer_provider_cost == Decimal(".02")
    assert rows[0].answer_customer_charge == Decimal(".04")
    assert rows[0].selector_provider_cost == charge.provider_cost
    assert rows[0].net_savings is None
    assert (
        await db.query_raw(
            "SELECT spend_exact::text AS spend FROM deltallm_verificationtoken WHERE token=$1",
            charge.attribution.api_key,
        )
        == before
    )
    journal = await db.query_raw(
        "SELECT answer_state FROM deltallm_billing_operations WHERE api_key=$1",
        charge.attribution.api_key,
    )
    assert journal[0]["answer_state"] == "unattempted"


@pytest.mark.parametrize("condition", ["missing", "unknown", "unpriced"])
async def test_missing_unknown_or_unpriced_answer_stays_pending_not_zero(operation_db, condition):
    db, _, charge = operation_db
    await reserve(db, charge)
    if condition != "missing":
        await write_answer(
            db, charge, reported=condition != "unknown", unpriced=condition == "unpriced"
        )
    _, rows = await observations(db, charge)
    assert rows[0].answer_provider_cost is rows[0].answer_customer_charge is None
    assert rows[0].answer_state is ComponentState.PENDING
    assert rows[0].selector_provider_cost == charge.provider_cost


async def test_late_answer_receipt_changes_pending_report_without_duplicate_selector_charge(
    operation_db,
):
    db, _, charge = operation_db
    await reserve(db, charge)
    _, first = await observations(db, charge)
    assert first[0].answer_state is ComponentState.PENDING
    await write_answer(db, charge)
    await write_answer(db, charge)
    _, second = await observations(db, charge)
    assert second[0].answer_state is ComponentState.SETTLED
    assert second[0].answer_provider_cost == Decimal(".02")
    assert second[0].selector_provider_cost == first[0].selector_provider_cost


async def test_reports_filter_other_tenants_before_page_and_use_stable_cursor(operation_db):
    db, _, charge = operation_db
    await reserve(db, charge)
    rows, _ = await observations(db, charge)
    assert len(rows) == 1
    forbidden, _ = await observations(
        db, charge, visibility=SpendVisibility(False, organization_ids=("other-org",))
    )
    assert forbidden == []
    denied, _ = await observations(db, charge, visibility=SpendVisibility(False))
    assert denied == []
    older, _ = await observations(
        db, charge, before=(rows[0]["created_at"], rows[0]["operation_id"])
    )
    assert older == []


async def test_report_page_uses_existing_scope_time_index_at_representative_cardinality(
    operation_db,
):
    db, _, charge = operation_db
    await reserve(db, charge)
    await db.execute_raw(
        """
        INSERT INTO deltallm_billing_operations
          (operation_id,owner_token,api_key,organization_id,model,snapshot,selector_event_id,
           selector_allowance,answer_allowance,created_at,expires_at,answer_state)
        SELECT o.operation_id||'-plan-'||n,o.owner_token,o.api_key,'other-'||n,o.model,o.snapshot,
          o.selector_event_id||'-plan-'||n,0,0,NOW()-interval '1 day',NOW()-interval '1 day'+interval '5 minutes','unattempted'
        FROM deltallm_billing_operations o CROSS JOIN generate_series(1,10000) n WHERE o.operation_id=$1
    """,
        str(charge.attribution.operation_id),
    )
    await db.execute_raw("ANALYZE deltallm_billing_operations")
    query = query_for(charge, limit=10)
    rows = await db.query_raw("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query.sql, *query.params)
    report = rows[0]["QUERY PLAN"][0]
    pending, nodes = [report["Plan"]], []
    while pending:
        node = pending.pop()
        nodes.append(node)
        pending.extend(node.get("Plans", []))
    operation_nodes = [
        node for node in nodes if node.get("Relation Name") == "deltallm_billing_operations"
    ]
    assert len(operation_nodes) == 1
    assert operation_nodes[0]["Node Type"] == "Index Scan"
    assert operation_nodes[0]["Index Name"] == "deltallm_billing_operations_org_time_idx"
    assert operation_nodes[0]["Actual Rows"] == 1
    assert report["Plan"]["Actual Rows"] <= 11
    print(
        json.dumps(
            {
                "execution_ms": report["Execution Time"],
                "operation_index": operation_nodes[0]["Index Name"],
                "operation_rows": operation_nodes[0]["Actual Rows"],
            }
        )
    )
