from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.batch.completion_outbox import BatchCompletionOutboxWorker
from src.batch.selector_identity import batch_selector_operation_id
from src.billing.operation_reservation import BillingOperationUnavailable, ComponentState
from src.billing.spend import SpendTrackingService
from src.billing.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.db.billing_operation_recovery import BillingOperationRecovery
from src.db.billing_operations import BillingOperationRepository
from src.services.spend_visibility import SpendVisibility
from tests import test_billing_operations_postgres as billing_fixtures
from tests.test_batch_completion_outbox import _build_record
from tests.test_billing_operations_postgres import deadline
from tests.test_routing_cost_reports_postgres import observations
from tests.test_soft_selector_operations_postgres import soft_operation

pytestmark = pytest.mark.postgres
selector_billing_db = billing_fixtures.selector_billing_db
operation_db = billing_fixtures.operation_db


async def test_rejected_admission_rolls_back_and_same_batch_identity_can_retry(operation_db):
    db, _, charge = operation_db
    store = BillingOperationRepository(db)
    operation = soft_operation(charge)
    await db.execute_raw(
        "UPDATE deltallm_verificationtoken SET max_budget=0 WHERE token=$1",
        charge.attribution.api_key,
    )
    with pytest.raises(BillingOperationUnavailable):
        await store.reserve(operation, expires_at=deadline())
    assert (
        await db.query_raw(
            "SELECT operation_id FROM deltallm_billing_operations WHERE operation_id=$1",
            str(operation.attribution.operation_id),
        )
        == []
    )
    await db.execute_raw(
        "UPDATE deltallm_verificationtoken SET max_budget=100 WHERE token=$1",
        charge.attribution.api_key,
    )
    retried = soft_operation(charge)
    result = await store.reserve(retried, expires_at=deadline())
    assert result.selector_state is ComponentState.RESERVED
    assert retried.attribution.operation_id == operation.attribution.operation_id


async def test_committed_admission_with_lost_response_cannot_be_adopted_by_retry(operation_db):
    db, _, charge = operation_db
    store = BillingOperationRepository(db)
    operation = soft_operation(charge)

    async def lost_response():
        await store.reserve(operation, expires_at=deadline())
        raise BillingOperationUnavailable()

    with pytest.raises(BillingOperationUnavailable):
        await lost_response()
    retry = soft_operation(charge)
    with pytest.raises(BillingOperationUnavailable):
        await store.reserve(retry, expires_at=deadline())
    with pytest.raises(BillingOperationUnavailable):
        await store.dispatch(retry, component="selector", expires_at=deadline())
    assert (
        await store.reserve(operation, expires_at=deadline())
    ).selector_state is ComponentState.RESERVED


async def test_selector_receipt_and_batch_answer_settle_once_and_join_in_scoped_report(
    operation_db,
):
    db, _, original = operation_db
    item_id = str(uuid4())
    operation_id = batch_selector_operation_id("b1", item_id)
    charge = original.model_copy(
        update={
            "attribution": original.attribution.model_copy(update={"operation_id": operation_id})
        }
    )
    store = BillingOperationRepository(db)
    operation = soft_operation(charge)
    await store.reserve(operation, expires_at=deadline())
    await store.dispatch(operation, component="selector", expires_at=deadline())
    await store.accept_selector(operation, charge, expires_at=deadline())
    service = SpendIngestionService(
        db_client=db,
        writer=SpendTrackingService(db),
        operation_recovery=BillingOperationRecovery(
            store, max_pending_events=100000, max_attempts=10, selector_events_only=True
        ),
        config=SpendIngestionConfig(enabled=True, worker_enabled=False),
    )
    try:
        await service._process_batch(await service._claim_batch())
        await store.accept_selector(operation, charge, expires_at=deadline())
        assert await service._claim_batch() == []
        _, pending = await observations(db, charge)
        assert pending[0].answer_provider_cost is None

        record = _build_record(
            completion_id=str(operation_id),
            payload_overrides={
                "item_id": item_id,
                "billing_event_id": str(operation_id),
                "api_key": charge.attribution.api_key,
                "user_id": charge.attribution.user_id,
                "team_id": charge.attribution.team_id,
                "organization_id": charge.attribution.organization_id,
                "owner_account_id": None,
                "owner_snapshot_complete": True,
                "model": charge.attribution.model_group,
                "call_type": "chat_batch",
                "billed_cost": 0.04,
                "provider_cost": 0.02,
                "pricing_metadata": {
                    "billing": {
                        "billing_unit": "token",
                        "usage_snapshot": {"prompt_tokens": 5, "completion_tokens": 0},
                    }
                },
            },
        )
        repository = SimpleNamespace(mark_completion_outbox_sent=AsyncMock(return_value=True))
        worker = BatchCompletionOutboxWorker(app=None, repository=repository)
        for _ in range(2):
            await worker._record_durable_success_with_dependencies(
                repository=repository,
                spend_tracking_service=SpendTrackingService(db),
                record=record,
                payload=record.payload_json,
            )
        rows = await db.query_raw(
            "SELECT id,spend_exact::text AS cost FROM deltallm_spendlog_events WHERE api_key=$1",
            charge.attribution.api_key,
        )
        assert {row["id"] for row in rows} == {
            str(operation_id),
            charge.attribution.component_event_id,
        }
        (ledger,) = await db.query_raw(
            "SELECT spend_exact::text AS spend FROM deltallm_verificationtoken WHERE token=$1",
            charge.attribution.api_key,
        )
        assert Decimal(ledger["spend"]) == Decimal("0.04") + charge.customer_charge
        _, reported = await observations(db, charge)
        assert reported[0].answer_provider_cost == Decimal("0.02")
        assert reported[0].answer_customer_charge == Decimal("0.04")
        assert reported[0].selector_provider_cost == charge.provider_cost
        assert reported[0].net_savings is None
        denied, _ = await observations(
            db, charge, visibility=SpendVisibility(False, organization_ids=("other",))
        )
        assert denied == []
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_spend_ingestion_outbox WHERE event_id=$1",
            charge.attribution.component_event_id,
        )
