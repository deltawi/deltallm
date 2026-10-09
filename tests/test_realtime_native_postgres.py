"""Real native Realtime shares signed transport, grants, facts, and recovery."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.billing.charges.realtime_accounting_bounds import RealtimeCostBounds
from src.billing.charges.realtime_native import NativeRealtimeBilling
from src.billing.charges.realtime_usage import RealtimeDurationUsage
from src.db.billing.realtime_billing import RealtimeBillingRepository
from src.realtime.errors import RealtimeError
from tests.realtime.test_pricing import charge_context, receipt
from tests.test_accounting_local_leases_postgres import deadline
from tests.test_accounting_protocol_postgres import accounting_db as _accounting_db
from tests.test_accounting_role_runtime_postgres import graph
from tests.test_selector_charge_db_integration import selector_billing_db as _identity_db
from tests.accounting_read_model_fixtures import wait_for_native_projection

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db
selector_billing_db = _identity_db


@pytest.mark.parametrize("profile", ["response", "transcription_tokens", "duration"])
async def test_native_realtime_exact_and_unknown_turns_reach_every_scope_and_reports(
    accounting_db, selector_billing_db, profile
):
    clients, generation = accounting_db
    db, identity = selector_billing_db
    key = identity.attribution.api_key
    context = charge_context(key)
    if profile == "duration":
        bounds = RealtimeCostBounds(transcription=True, input_seconds=Decimal(2))
    else:
        bounds = RealtimeCostBounds(
            transcription=profile == "transcription_tokens", input_tokens=100, output_tokens=10
        )
    context = replace(context, cost_bounds=bounds)
    for table, column in (
        ("deltallm_verificationtoken", "token"),
        ("deltallm_usertable", "user_id"),
        ("deltallm_teamtable", "team_id"),
        ("deltallm_organizationtable", "organization_id"),
    ):
        await db.execute_raw(f"UPDATE {table} SET max_budget=.05 WHERE {column}=$1", key)
    await db.execute_raw(
        "UPDATE deltallm_teamtable SET model_max_budget=jsonb_build_object('voice',.05) WHERE team_id=$1",
        key,
    )
    projection, request, api, transport, wire, projected = await graph(clients, generation)
    try:
        await projection.start(expires_at=deadline())
        await request.start(expires_at=deadline())
        await api.start(expires_at=deadline())
        native = NativeRealtimeBilling(api.local.service, RealtimeBillingRepository(db))
        await native.check_owner(context)
        operations = [str(uuid4()), str(uuid4())]
        for operation in operations:
            await native.dispatch(
                operation, context, expires_at=datetime.now(UTC) + timedelta(minutes=5)
            )
        usage = receipt(context.attribution.session_id)
        if profile == "duration":
            usage = replace(
                usage, operation="transcription", usage=RealtimeDurationUsage(Decimal("1.5"))
            )
        elif profile == "transcription_tokens":
            usage = replace(
                usage,
                operation="transcription",
                usage=replace(usage.usage, output_text=7, output_audio=0),
            )
        await asyncio.gather(*(native.accept(operations[0], context, usage) for _ in range(3)))
        # No receipt for the second turn. Disconnect keeps its full allowance.
        await native.close(context.attribution.session_id)
        await api.close(expires_at=deadline())
        await wait_for_native_projection(clients[0], projected.progress, generation, timeout=2)
        expected = context.customer.cost(usage.usage)
        rows = await db.query_raw(
            "SELECT scope_type,committed_exact::text AS committed,"
            "reserved_exact::text AS reserved,provisional_exact::text AS provisional "
            "FROM deltallm_accounting_budget_windows WHERE generation=$1 AND scope_id=ANY($2::text[])",
            generation,
            [key, f"{key}:voice"],
        )
        assert {row["scope_type"] for row in rows} == {
            "api_key",
            "user",
            "team",
            "organization",
            "team_model",
        }
        assert len(rows) == 5
        for row in rows:
            assert Decimal(row["committed"]) == expected
            assert Decimal(row["reserved"]) == 0
            assert Decimal(row["provisional"]) == bounds.allowance(context.customer)
        facts = await db.query_raw(
            "SELECT request_id,spend_exact::text AS spend,call_type "
            "FROM deltallm_accounting_usage_facts_v2 WHERE protocol_generation=$1 AND api_key=$2",
            generation,
            key,
        )
        assert facts == [
            {
                "request_id": operations[0],
                "spend": str(expected),
                "call_type": "realtime_response"
                if profile == "response"
                else "realtime_transcription",
            }
        ]
        assert (
            await db.query_raw(
                "SELECT operation_id FROM deltallm_realtime_billing_intents WHERE session_id=$1",
                context.attribution.session_id,
            )
            == []
        )
        assert (
            await db.query_raw("SELECT id FROM deltallm_spendlog_events WHERE api_key=$1", key)
            == []
        )
        assert sum("/allocate/" in path for path in wire.calls) == 1
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


async def test_native_identity_check_still_denies_a_foreign_owner(
    accounting_db, selector_billing_db
):
    _, _ = accounting_db
    db, identity = selector_billing_db
    context = replace(
        charge_context(identity.attribution.api_key),
        cost_bounds=RealtimeCostBounds(input_tokens=100, output_tokens=10),
    )
    service = AsyncMock()
    native = NativeRealtimeBilling(service, RealtimeBillingRepository(db))
    foreign = replace(context, attribution=replace(context.attribution, organization_id="other"))
    with pytest.raises(RealtimeError, match="access"):
        await native.check_owner(foreign)
    service.reserve.assert_not_awaited()
