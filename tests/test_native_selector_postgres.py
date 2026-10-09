"""Native selector and answer charges use one scope authority and exact reports."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.accounting.accounting_admission import (
    admit_accounting_reservation,
    reservation_audit_envelope,
)
from src.billing.accounting.accounting_finalization import accounting_audit_envelope
from src.billing.accounting.accounting_protocol import AccountingAttempt, request_fingerprint
from src.billing.accounting.journal.accounting_terminal_preparation import prepare_accounting_charge
from src.billing.charges.operation_reservation import SoftSelectorOperation, token_price_allowance
from src.billing.charges.selector_native import NativeSelectorBilling
from src.db.routing_costs import routing_cost_query
from src.services.spend_visibility import SpendVisibility
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_protocol_postgres import accounting_db as _accounting_db
from tests.test_accounting_role_runtime_postgres import graph
from tests.test_selector_charge_db_integration import selector_billing_db as _identity_db
from tests.accounting_read_model_fixtures import wait_for_native_projection

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db
selector_billing_db = _identity_db


@pytest.mark.parametrize("selector_result", ["completed", "unknown", "unsent"])
async def test_native_components_share_scope_totals_and_bounded_reporting(
    accounting_db, selector_billing_db, selector_result
):
    clients, generation = accounting_db
    db, charge = selector_billing_db
    owner, key = charge.attribution, charge.attribution.api_key
    for table, column in (
        ("deltallm_verificationtoken", "token"),
        ("deltallm_usertable", "user_id"),
        ("deltallm_teamtable", "team_id"),
        ("deltallm_organizationtable", "organization_id"),
    ):
        await db.execute_raw(f"UPDATE {table} SET max_budget=.1 WHERE {column}=$1", key)
    await db.execute_raw(
        "UPDATE deltallm_teamtable SET model_max_budget=jsonb_build_object($2::text,.1) WHERE team_id=$1",
        key,
        owner.model_group,
    )
    operation = SoftSelectorOperation(
        attribution=owner,
        owner_token=uuid4(),
        pricing=charge.pricing,
        admission_allowance=token_price_allowance(
            charge.pricing, input_tokens=1000, output_tokens=64
        ),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    projection, request, api, transport, _, settled = await graph(clients, generation)
    try:
        await projection.start(expires_at=deadline())
        await request.start(expires_at=deadline())
        await api.start(expires_at=deadline())
        service = api.local.service
        store = NativeSelectorBilling(service).bind(operation, max_input_tokens=1000)
        await store.reserve(operation, expires_at=deadline())
        if selector_result != "unsent":
            await store.dispatch(operation, component="selector", expires_at=deadline())
        if selector_result == "completed":
            await asyncio.gather(
                *(store.accept_selector(operation, charge, expires_at=deadline()) for _ in range(3))
            )
        await store.close()
        selector_handle = store._handle()
        attribution = selector_handle.reservation.attribution.model_copy(
            update={"call_type": "chat_completion"}
        )
        allowance = Decimal(".001")
        reservation = selector_handle.reservation.model_copy(
            update={
                "operation_id": owner.operation_id,
                "owner_token": uuid4(),
                "request_fingerprint": request_fingerprint(operation_kind="answer", payload={}),
                "attribution": attribution,
                "allowance": allowance,
                "pricing_snapshot": {"selector_event_id": owner.component_event_id},
                "audit_envelope": reservation_audit_envelope(
                    attribution, operation_id=owner.operation_id, allowance=allowance
                ),
            }
        )
        answer = await admit_accounting_reservation(
            service,
            reservation=reservation,
            attempt=AccountingAttempt(
                deployment_id=owner.deployment_id,
                provider=owner.provider,
                model=owner.model_group,
                pricing_snapshot={},
            ),
        )
        payload = charge.spend_payload() | {
            "request_id": str(owner.operation_id),
            "call_type": "chat_completion",
            "cost_exact": ".00005",
            "provider_cost_exact": ".00002",
        }
        terminal = prepare_accounting_charge(
            answer,
            payload=payload,
            occurred_at=datetime.now(UTC),
            audit_envelope=accounting_audit_envelope(
                answer, event_id=uuid4(), status="success", metadata={}
            ),
        )
        await service.finalize_operation(answer, terminal)
        await api.close(expires_at=deadline())
        await wait_for_native_projection(clients[0], settled.progress, generation, timeout=2)
        expected = Decimal(".00005") + (
            charge.customer_charge if selector_result == "completed" else Decimal(0)
        )
        windows = await db.query_raw(
            "SELECT scope_type,committed_exact::text AS committed,reserved_exact::text AS reserved,"
            "provisional_exact::text AS provisional FROM deltallm_accounting_budget_windows "
            "WHERE generation=$1 AND scope_id=ANY($2::text[])",
            generation,
            [key, f"{key}:{owner.model_group}"],
        )
        assert len(windows) == 5
        for window in windows:
            assert Decimal(window["committed"]) == expected
            assert Decimal(window["reserved"]) == 0
            assert Decimal(window["provisional"]) == (
                operation.admission_allowance if selector_result == "unknown" else Decimal(0)
            )
        end = datetime.now(UTC) + timedelta(minutes=1)
        query = routing_cost_query(
            visibility=SpendVisibility(True),
            start=end - timedelta(days=1),
            end=end,
            model_group=owner.model_group,
            limit=10,
        )
        reports = await db.query_raw(query.sql, *query.params)
        assert len(reports) == 1 and reports[0]["operation_id"] == str(owner.operation_id)
        report = reports[0]
        assert Decimal(report["answer_customer_charge"]) == Decimal(".00005")
        assert (
            report["selector_state"]
            == {"completed": "settled", "unsent": "unattempted", "unknown": "pending"}[
                selector_result
            ]
        )
        if selector_result == "unknown":
            assert report["selector_customer_charge"] is report["selector_provider_cost"] is None
        else:
            assert Decimal(report["selector_customer_charge"]) == (
                charge.customer_charge if selector_result == "completed" else Decimal(0)
            )
        assert (
            await db.query_raw("SELECT id FROM deltallm_spendlog_events WHERE api_key=$1", key)
            == []
        )
        # A stale or foreign component cannot donate its charge to this answer.
        await db.execute_raw(
            "UPDATE deltallm_billing_operations SET snapshot=jsonb_set("
            "snapshot,'{pricing_snapshot,parent_event_id}','\"foreign\"') WHERE operation_id=$1",
            owner.component_event_id,
        )
        missing = (await db.query_raw(query.sql, *query.params))[0]
        assert missing["selector_customer_charge"] is None
        assert missing["selector_state"] == "pending"
    finally:
        try:
            await api.close(expires_at=deadline())
        finally:
            try:
                await request.close(expires_at=deadline())
            finally:
                try:
                    await projection.close(expires_at=deadline())
                finally:
                    await transport.close()
