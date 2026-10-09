import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.charges.operation_reservation import BillingOperationUnavailable
from src.billing.spend.spend import SpendTrackingService
from src.billing.spend.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.db.realtime_billing import RealtimeBillingRepository
from src.db.realtime_recovery import RealtimeBillingRecovery
from src.realtime.errors import RealtimeError
from tests import test_selector_charge_db_integration as fixtures
from tests.realtime.test_pricing import charge_context, receipt

selector_billing_db = fixtures.selector_billing_db
pytestmark = [pytest.mark.integration, pytest.mark.postgres]


@pytest.fixture
async def realtime_db(selector_billing_db):
    db, selector = selector_billing_db
    context = charge_context(selector.attribution.api_key)
    try:
        yield db, context
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_spend_ingestion_outbox WHERE event_id IN "
            "(SELECT event_id FROM deltallm_realtime_billing_intents WHERE session_id=$1)",
            context.attribution.session_id,
        )
        await db.execute_raw(
            "WITH removed AS (DELETE FROM deltallm_realtime_billing_intents WHERE session_id=$1 RETURNING state) "
            "UPDATE deltallm_telemetry_ingestion_capacity SET pending_count=pending_count-"
            "(SELECT COUNT(*) FROM removed WHERE state<>'settled') WHERE queue_name='realtime_billing'",
            context.attribution.session_id,
        )


def ingestion(db):
    service = SpendIngestionService(
        db_client=db,
        writer=SpendTrackingService(db),
        config=SpendIngestionConfig(enabled=True, worker_enabled=False),
    )
    service.realtime_recovery = RealtimeBillingRecovery(
        db, max_pending_events=100000, max_attempts=10
    )
    return service


async def dispatch(db, context, *, max_pending=100000):
    operation = str(uuid4())
    await RealtimeBillingRepository(db, max_pending=max_pending).dispatch(
        operation, context, expires_at=datetime.now(UTC) + timedelta(minutes=5)
    )
    return operation


async def test_receipt_survives_restart_and_settles_every_scope_once(realtime_db):
    db, context = realtime_db
    repository = RealtimeBillingRepository(db)
    await repository.check_owner(context)
    operation = await dispatch(db, context)
    usage = receipt(context.attribution.session_id)
    await asyncio.gather(*(repository.accept(operation, context, usage) for _ in range(3)))
    recovered = ingestion(db)
    records = await recovered._claim_batch()
    assert [record.event_id for record in records] == [usage.receipt_id]
    await recovered._process_batch(records)
    await repository.accept(operation, context, usage)
    assert await recovered._claim_batch() == []
    assert await fixtures._scope_totals(db, context.attribution.api_key) == {
        scope: Decimal("0.0000839") for scope in ("key", "user", "team", "org", "model")
    }
    rows = await db.query_raw(
        "SELECT state FROM deltallm_realtime_billing_intents WHERE operation_id=$1", operation
    )
    assert rows == [{"state": "settled"}]


async def test_ambiguous_dispatch_is_never_replayed_and_missing_usage_stays_pending(realtime_db):
    db, context = realtime_db
    operation = await dispatch(db, context)
    repository = RealtimeBillingRepository(db)
    with pytest.raises(BillingOperationUnavailable):
        await repository.dispatch(
            operation, context, expires_at=datetime.now(UTC) + timedelta(minutes=5)
        )
    await repository.close(context.attribution.session_id)
    assert await ingestion(db)._claim_batch() == []
    rows = await db.query_raw(
        "SELECT state,spend_payload FROM deltallm_realtime_billing_intents WHERE operation_id=$1",
        operation,
    )
    assert rows == [{"state": "pending", "spend_payload": None}]
    # A retained late receipt is recoverable without replaying provider work.
    await repository.accept(operation, context, receipt(context.attribution.session_id))
    assert len(await ingestion(db)._claim_batch()) == 1


async def test_conflicting_receipt_does_not_replace_authoritative_facts(realtime_db):
    db, context = realtime_db
    operation = await dispatch(db, context)
    repository = RealtimeBillingRepository(db)
    usage = receipt(context.attribution.session_id)
    await repository.accept(operation, context, usage)
    conflict = replace(usage, usage=replace(usage.usage, output_text=999))
    with pytest.raises(BillingOperationUnavailable):
        await repository.accept(operation, context, conflict)
    worker = ingestion(db)
    await worker.realtime_recovery.recover()
    # Millisecond rounding can put a new due time after the next instant.
    # This case tests receipt conflicts, not the scheduler's due-time boundary.
    await db.execute_raw(
        "UPDATE deltallm_spend_ingestion_outbox "
        "SET next_attempt_at=NOW()-INTERVAL '1 second' WHERE event_id=$1",
        usage.receipt_id,
    )
    records = await worker._claim_batch()
    assert [record.event_id for record in records] == [usage.receipt_id]
    assert Decimal(records[0].payload["cost_exact"]) == Decimal("0.0000839")


@pytest.mark.parametrize("duration", [False, True])
async def test_turn_timestamps_survive_duplicate_receipts_and_recovery(realtime_db, duration):
    from src.billing.charges.realtime_usage import RealtimeDurationUsage

    db, context = realtime_db
    context = replace(context, started_at=datetime.now(UTC) - timedelta(minutes=2))
    repository = RealtimeBillingRepository(db)
    operation = await dispatch(db, context)
    usage = receipt(context.attribution.session_id)
    if duration:
        usage = replace(
            usage, operation="transcription", usage=RealtimeDurationUsage(Decimal("2.4"))
        )
    rows = await db.query_raw(
        "SELECT created_at FROM deltallm_realtime_billing_intents WHERE operation_id=$1", operation
    )
    started_at = datetime.fromisoformat(rows[0]["created_at"])
    await asyncio.gather(*(repository.accept(operation, context, usage) for _ in range(3)))
    accepted = await db.query_raw(
        "SELECT spend_payload FROM deltallm_realtime_billing_intents WHERE operation_id=$1",
        operation,
    )
    payload = accepted[0]["spend_payload"]
    assert datetime.fromisoformat(payload["start_time"]) == started_at
    assert datetime.fromisoformat(payload["end_time"]) >= started_at
    assert datetime.fromisoformat(payload["end_time"]) - started_at < timedelta(seconds=5)
    # A later duplicate must preserve the first accepted timestamps exactly.
    await repository.accept(operation, context, usage)
    worker = ingestion(db)
    records = await worker._claim_batch()
    assert records[0].payload == payload
    await worker._process_batch(records)
    written = await db.query_raw(
        "SELECT start_time,end_time,latency_ms,spend_exact::text AS cost "
        "FROM deltallm_spendlog_events WHERE id=$1",
        usage.receipt_id,
    )
    # The canonical ledger retains milliseconds; the journal retains microseconds.
    assert abs(datetime.fromisoformat(written[0]["start_time"]) - started_at) <= timedelta(
        microseconds=500
    )
    assert 0 <= written[0]["latency_ms"] < 5000
    assert Decimal(written[0]["cost"]) == context.customer.cost(usage.usage)


async def test_turn_reporting_uses_its_own_day_and_month(realtime_db):
    db, context = realtime_db
    month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    context = replace(context, started_at=month - timedelta(minutes=2))
    repository = RealtimeBillingRepository(db)
    events = []
    for index, started_at in enumerate(
        (month - timedelta(seconds=1), month + timedelta(seconds=1))
    ):
        operation = await dispatch(db, context)
        await db.execute_raw(
            "UPDATE deltallm_realtime_billing_intents SET created_at=$2::timestamptz, "
            "expires_at=$2::timestamptz+INTERVAL '5 minutes' WHERE operation_id=$1",
            operation,
            started_at.isoformat(),
        )
        usage = replace(
            receipt(context.attribution.session_id),
            receipt_id=str(uuid4()),
            provider_id=f"response-{index}",
        )
        await repository.accept(operation, context, usage)
        events.append(usage.receipt_id)
    worker = ingestion(db)
    await worker._process_batch(await worker._claim_batch())
    rows = await db.query_raw(
        "SELECT DATE(start_time)::text AS day,DATE_TRUNC('month',start_time)::date::text AS month, "
        "COUNT(*)::int AS count FROM deltallm_spendlog_events WHERE id=ANY($1::text[]) "
        "GROUP BY 1,2 ORDER BY 1",
        events,
    )
    assert rows == [
        {
            "day": (month - timedelta(seconds=1)).date().isoformat(),
            "month": (month - timedelta(seconds=1)).replace(day=1).date().isoformat(),
            "count": 1,
        },
        {"day": month.date().isoformat(), "month": month.date().isoformat(), "count": 1},
    ]


async def test_older_accepted_timestamps_are_not_rebuilt(realtime_db):
    db, context = realtime_db
    context = replace(context, started_at=datetime.now(UTC) - timedelta(minutes=2))
    repository = RealtimeBillingRepository(db)
    operation = await dispatch(db, context)
    usage = receipt(context.attribution.session_id)
    await repository.accept(operation, context, usage)
    # An older writer froze session-based timing without the new metadata fields.
    older_payload = context.spend_payload(
        usage, operation_started_at=context.started_at, completed_at=datetime.now(UTC)
    )
    older_payload["metadata"].pop("realtime_session_started_at")
    older_payload["metadata"].pop("realtime_timing_basis")
    await db.execute_raw(
        "UPDATE deltallm_realtime_billing_intents SET spend_payload=$2::jsonb "
        "WHERE operation_id=$1",
        operation,
        json.dumps(older_payload),
    )
    await repository.accept(operation, context, usage)
    worker = ingestion(db)
    records = await worker._claim_batch()
    assert records[0].payload == older_payload
    await worker._process_batch(records)
    assert await worker._claim_batch() == []


async def test_capacity_failure_rolls_back_intent(realtime_db):
    db, context = realtime_db
    await dispatch(db, context, max_pending=1)
    with pytest.raises(BillingOperationUnavailable):
        await dispatch(db, context, max_pending=1)
    assert (
        len(
            await db.query_raw(
                "SELECT operation_id FROM deltallm_realtime_billing_intents WHERE session_id=$1",
                context.attribution.session_id,
            )
        )
        == 1
    )


@pytest.mark.parametrize("scope", ["key", "user", "team", "org", "model"])
async def test_every_shared_budget_scope_fails_explicitly(realtime_db, scope):
    db, context = realtime_db
    tables = {
        "key": ("deltallm_verificationtoken", "token"),
        "user": ("deltallm_usertable", "user_id"),
        "team": ("deltallm_teamtable", "team_id"),
        "org": ("deltallm_organizationtable", "organization_id"),
    }
    if scope == "model":
        await db.execute_raw(
            "UPDATE deltallm_teamtable SET model_max_budget='{\"voice\":100}'::jsonb WHERE team_id=$1",
            context.attribution.team_id,
        )
    else:
        table, key = tables[scope]
        await db.execute_raw(
            f"UPDATE {table} SET max_budget=100 WHERE {key}=$1", context.attribution.api_key
        )
    with pytest.raises(RealtimeError, match="Budget-capped"):
        await RealtimeBillingRepository(db).check_owner(context)


async def test_older_spend_worker_can_settle_before_realtime_extension_recovers(realtime_db):
    db, context = realtime_db
    operation = await dispatch(db, context)
    usage = receipt(context.attribution.session_id)
    await RealtimeBillingRepository(db).accept(operation, context, usage)
    upgraded = ingestion(db)
    await upgraded.realtime_recovery.recover()
    older = ingestion(db)
    older.realtime_recovery = None
    records = await older._claim_batch()
    await older._process_batch(records)
    before = await db.query_raw(
        "SELECT state FROM deltallm_realtime_billing_intents WHERE operation_id=$1", operation
    )
    assert before == [{"state": "accepted"}]
    await upgraded.realtime_recovery.recover()
    after = await db.query_raw(
        "SELECT state FROM deltallm_realtime_billing_intents WHERE operation_id=$1", operation
    )
    assert after == [{"state": "settled"}]
    assert await fixtures._scope_totals(db, context.attribution.api_key) == {
        scope: Decimal("0.0000839") for scope in ("key", "user", "team", "org", "model")
    }


@pytest.mark.parametrize("conflict", ["cost", "owner"])
async def test_older_worker_conflicts_are_quarantined_without_blocking_recovery(
    realtime_db, conflict
):
    db, context = realtime_db
    operation = await dispatch(db, context)
    usage = receipt(context.attribution.session_id)
    await RealtimeBillingRepository(db).accept(operation, context, usage)
    service = ingestion(db)
    await service.realtime_recovery.recover()
    older = ingestion(db)
    older.realtime_recovery = None
    await older._process_batch(await older._claim_batch())
    if conflict == "cost":
        await db.execute_raw(
            "UPDATE deltallm_spendlog_events SET spend_exact=NULL WHERE id=$1", usage.receipt_id
        )
    else:
        await db.execute_raw(
            "UPDATE deltallm_spendlog_events SET user_id=NULL WHERE id=$1", usage.receipt_id
        )
    await service.realtime_recovery.recover()
    rows = await db.query_raw(
        "SELECT state,pending_reason FROM deltallm_realtime_billing_intents WHERE operation_id=$1",
        operation,
    )
    assert rows == [{"state": "pending", "pending_reason": "ledger_conflict"}]


async def test_expired_owner_retains_unresolved_liability_without_provider_replay(realtime_db):
    db, context = realtime_db
    operation = await dispatch(db, context)
    await db.execute_raw(
        "UPDATE deltallm_realtime_billing_intents SET created_at=NOW()-INTERVAL '2 minutes', "
        "expires_at=NOW()-INTERVAL '1 minute' WHERE operation_id=$1",
        operation,
    )
    assert await ingestion(db)._claim_batch() == []
    rows = await db.query_raw(
        "SELECT state,pending_reason FROM deltallm_realtime_billing_intents WHERE operation_id=$1",
        operation,
    )
    assert rows == [{"state": "pending", "pending_reason": "owner_expired"}]
    with pytest.raises(BillingOperationUnavailable):
        await dispatch(db, context, max_pending=1)


@pytest.mark.parametrize("inherited_from", ["user", "service_account"])
async def test_effective_inherited_team_uses_the_normal_key_scope(realtime_db, inherited_from):
    db, context = realtime_db
    owner = context.attribution
    await db.execute_raw(
        "UPDATE deltallm_verificationtoken SET team_id=NULL WHERE token=$1", owner.api_key
    )
    if inherited_from == "service_account":
        await db.execute_raw(
            "INSERT INTO deltallm_serviceaccount(service_account_id,team_id,name,updated_at) VALUES ($1,$1,'realtime-test',NOW())",
            owner.team_id,
        )
        await db.execute_raw(
            "UPDATE deltallm_verificationtoken SET user_id=NULL,owner_service_account_id=$1 WHERE token=$1",
            owner.api_key,
        )
        context = replace(context, attribution=replace(owner, user_id=None))
    repository = RealtimeBillingRepository(db)
    await repository.check_owner(context)
    if inherited_from == "user":
        await db.execute_raw(
            "UPDATE deltallm_usertable SET team_id=NULL WHERE user_id=$1", owner.user_id
        )
    else:
        await db.execute_raw(
            "UPDATE deltallm_serviceaccount SET is_active=false WHERE service_account_id=$1",
            owner.team_id,
        )
    with pytest.raises(RealtimeError, match="access is unavailable"):
        await repository.check_owner(context)
