from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.spend import SpendTrackingService
from src.billing.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.db.spend_ingestion import SpendIngestionRepository
from tests.test_selector_charge import make_selector_charge
from tests.test_telemetry_ingestion_db_integration import _connect_prisma

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


@pytest.fixture
async def selector_billing_db():
    db = await _connect_prisma()
    identity = str(uuid4())
    charge = make_selector_charge()
    charge = charge.model_copy(
        update={
            "attribution": charge.attribution.model_copy(
                update={
                    "api_key": identity,
                    "user_id": identity,
                    "team_id": identity,
                    "organization_id": identity,
                    "owner_account_id": None,
                }
            )
        }
    )
    try:
        await db.execute_raw(
            """
            WITH org AS (
                INSERT INTO deltallm_organizationtable (id, organization_id)
                VALUES ($1, $1) RETURNING organization_id
            ), team AS (
                INSERT INTO deltallm_teamtable (team_id, organization_id, models)
                SELECT $1, organization_id, ARRAY[]::text[] FROM org RETURNING team_id
            ), principal AS (
                INSERT INTO deltallm_usertable (user_id, team_id, models)
                SELECT $1, team_id, ARRAY[]::text[] FROM team RETURNING user_id, team_id
            )
            INSERT INTO deltallm_verificationtoken (id, token, user_id, team_id, models)
            SELECT $1, $1, user_id, team_id, ARRAY[]::text[] FROM principal
            """,
            identity,
        )
        yield db, charge
    finally:
        async with db.tx() as tx:
            await tx.execute_raw(
                "DELETE FROM deltallm_spend_ingestion_outbox WHERE event_id = $1",
                charge.attribution.component_event_id,
            )
            await tx.execute_raw(
                "DELETE FROM deltallm_spendlog_events WHERE api_key = $1", identity
            )
            await tx.execute_raw("DELETE FROM deltallm_teammodelspend WHERE team_id = $1", identity)
            await tx.execute_raw(
                "DELETE FROM deltallm_verificationtoken WHERE token = $1", identity
            )
            await tx.execute_raw("DELETE FROM deltallm_usertable WHERE user_id = $1", identity)
            await tx.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", identity)
            await tx.execute_raw(
                "DELETE FROM deltallm_organizationtable WHERE organization_id = $1", identity
            )
        await SpendIngestionRepository(db).reconcile_capacity()
        await db.disconnect()


async def _scope_totals(db, identity):
    rows = await db.query_raw(
        """
        SELECT 'key' AS scope, spend_exact::text AS spend FROM deltallm_verificationtoken WHERE token = $1
        UNION ALL SELECT 'user', spend_exact::text FROM deltallm_usertable WHERE user_id = $1
        UNION ALL SELECT 'team', spend_exact::text FROM deltallm_teamtable WHERE team_id = $1
        UNION ALL SELECT 'org', spend_exact::text FROM deltallm_organizationtable WHERE organization_id = $1
        UNION ALL SELECT 'model', spend_exact::text FROM deltallm_teammodelspend WHERE team_id = $1
        """,
        identity,
    )
    return {row["scope"]: Decimal(row["spend"] or "0") for row in rows}


def _ingestion(db):
    return SpendIngestionService(
        db_client=db,
        writer=SpendTrackingService(db),
        config=SpendIngestionConfig(enabled=True, worker_enabled=False),
    )


@pytest.mark.asyncio
async def test_selector_receipt_survives_ingress_restart_and_answer_failure(selector_billing_db):
    db, charge = selector_billing_db
    ingress = _ingestion(db)
    await ingress.log_selector_charge(charge, expires_at=asyncio.get_running_loop().time() + 2)
    await ingress.log_selector_charge(charge, expires_at=asyncio.get_running_loop().time() + 2)
    # A fresh owner/process consumes the durable receipt, not in-memory state.
    recovered = _ingestion(db)
    records = await recovered._claim_batch()
    assert [record.event_id for record in records] == [charge.attribution.component_event_id]
    await recovered._process_batch(records)
    await recovered.log_selector_charge(charge, expires_at=asyncio.get_running_loop().time() + 2)
    assert await recovered._claim_batch() == []
    assert await _scope_totals(db, charge.attribution.api_key) == {
        scope: charge.customer_charge for scope in ("key", "user", "team", "org", "model")
    }
    await SpendTrackingService(db).log_request_failure_once(
        event_id=str(charge.attribution.operation_id),
        request_id=str(charge.attribution.operation_id),
        api_key=charge.attribution.api_key,
        user_id=charge.attribution.user_id,
        team_id=charge.attribution.team_id,
        organization_id=charge.attribution.organization_id,
        end_user_id=None,
        model=charge.attribution.model_group,
        call_type="chat_completion",
        http_status_code=500,
        error_type="answer_failed",
    )
    rows = await db.query_raw(
        "SELECT id, spend_exact::text AS spend, provider_cost_exact::text AS provider_cost FROM deltallm_spendlog_events WHERE api_key = $1",
        charge.attribution.api_key,
    )
    assert len(rows) == 2
    selector = next(row for row in rows if row["id"] == charge.attribution.component_event_id)
    assert (
        Decimal(selector["spend"]) == Decimal(selector["provider_cost"]) == charge.customer_charge
    )
    assert (await _scope_totals(db, charge.attribution.api_key))["key"] == charge.customer_charge


@pytest.mark.asyncio
async def test_concurrent_selector_receipt_delivery_has_one_economic_effect(selector_billing_db):
    db, charge = selector_billing_db

    async def deliver():
        async with db.tx(timeout=timedelta(seconds=10)) as tx:
            return await SpendTrackingService(tx).log_batch_once(
                [(charge.attribution.component_event_id, "spend", charge.spend_payload())]
            )

    results = await asyncio.gather(*(deliver() for _ in range(8)))
    assert sum(bool(inserted) for inserted, _ in results) == 1
    assert await _scope_totals(db, charge.attribution.api_key) == {
        scope: charge.customer_charge for scope in ("key", "user", "team", "org", "model")
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
async def test_selector_transaction_rollback_then_replay_is_exact(selector_billing_db, error):
    db, charge = selector_billing_db
    event = (charge.attribution.component_event_id, "spend", charge.spend_payload())
    with pytest.raises(error):
        async with db.tx() as tx:
            await SpendTrackingService(tx).log_batch_once([event])
            raise error()
    assert (
        await db.query_raw("SELECT id FROM deltallm_spendlog_events WHERE id = $1", event[0]) == []
    )
    assert all(
        value == 0 for value in (await _scope_totals(db, charge.attribution.api_key)).values()
    )
    async with db.tx() as tx:
        inserted, _ = await SpendTrackingService(tx).log_batch_once([event])
    assert inserted == {event[0]}
    assert await _scope_totals(db, charge.attribution.api_key) == {
        scope: charge.customer_charge for scope in ("key", "user", "team", "org", "model")
    }
