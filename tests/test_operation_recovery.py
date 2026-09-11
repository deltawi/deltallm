import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.billing.operation_reservation import BillingOperationUnavailable
from src.billing.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.db.billing_operation_recovery import BillingOperationRecovery
from src.db.billing_operations import BillingOperationRepository
from src.db.spend_ingestion import SpendIngestionRepository
from tests.test_billing_operation_repository import transaction_mock
from tests.test_spend_ingestion import _OutboxDB, _Writer


async def test_recovery_unavailable_does_not_starve_durable_spend_delivery():
    recovery = AsyncMock()
    recovery.recover.side_effect = BillingOperationUnavailable()
    service = SpendIngestionService(
        db_client=_OutboxDB(),
        writer=_Writer(),
        operation_recovery=recovery,
        config=SpendIngestionConfig(enabled=True, worker_enabled=False),
    )
    service.repository.claim_batch = AsyncMock(return_value=[])
    assert await service._claim_batch() == []
    service.repository.claim_batch.assert_awaited_once()
    recovery.recover.assert_awaited_once_with()


def recovery_with_transactions(*contexts):
    db = MagicMock()
    db.tx.side_effect = contexts
    return BillingOperationRecovery(
        BillingOperationRepository(db), max_pending_events=1000, max_attempts=10
    )


def receipt_row():
    return {
        "operation_id": "private-operation",
        "selector_event_id": "private-child",
        "selector_state": "accepted",
        "selector_receipt": {"api_key": "private-key", "cost_exact": "0.01"},
    }


async def test_recovery_reconciles_accounts_before_outbox_admission(monkeypatch):
    first, context = transaction_mock([[], [receipt_row()], [{"outcome": "recovered"}]])
    second, empty = transaction_mock([[], []])

    async def enqueue(**kwargs):
        assert "deltallm_recover_operation_isolated" in first.query_raw.call_args.args[0]
        assert kwargs["event_id"] == "private-child"

    enqueued = AsyncMock(side_effect=enqueue)
    monkeypatch.setattr(SpendIngestionRepository, "enqueue", enqueued)
    assert await recovery_with_transactions(context, empty).recover() == 1
    enqueued.assert_awaited_once()
    assert second.query_raw.call_args.args[1] == "private-operation"
    assert "recovery_blocked_at IS NULL" in second.query_raw.call_args.args[0]


@pytest.mark.parametrize("outcome", ["receipt_conflict", "integrity_failure"])
async def test_quarantine_skips_enqueue_and_reports_only_committed_allowlisted_cause(
    monkeypatch, caplog, outcome
):
    _, context = transaction_mock([[], [receipt_row()], [{"outcome": outcome}]])
    second, empty = transaction_mock([[], []])
    enqueue = AsyncMock()
    monkeypatch.setattr(SpendIngestionRepository, "enqueue", enqueue)
    metric = MagicMock()
    monkeypatch.setattr(
        "src.db.billing_operation_recovery.increment_spend_ingestion_failure", metric
    )
    assert await recovery_with_transactions(context, empty).recover() == 1
    enqueue.assert_not_awaited()
    metric.assert_called_once_with(f"operation_recovery_{outcome}")
    assert caplog.records[-1].cause == outcome
    assert "private" not in caplog.text
    assert second.query_raw.await_count == 2


@pytest.mark.parametrize("failure_at", ["select", "reconcile", "commit"])
async def test_transient_failure_still_attempts_other_lane_without_retrying_row(
    monkeypatch, caplog, failure_at
):
    results = [[], [receipt_row()], [{"outcome": "receipt_conflict"}]]
    if failure_at != "commit":
        results[1 if failure_at == "select" else 2] = RuntimeError("private-backend-text")
    _, context = transaction_mock(results)
    if failure_at == "commit":
        context.__aexit__.side_effect = RuntimeError("private-backend-text")
    second, empty = transaction_mock([[], []])
    with pytest.raises(BillingOperationUnavailable):
        await recovery_with_transactions(context, empty).recover()
    assert second.query_raw.await_count == 2
    assert second.query_raw.call_args.args[1] == (
        None if failure_at == "select" else "private-operation"
    )
    assert "billing_operation_recovery_blocked" not in caplog.text
    assert "private" not in caplog.text


async def test_cancellation_rolls_back_and_does_not_start_the_other_lane():
    _, context = transaction_mock([[], [receipt_row()], asyncio.CancelledError()])
    recovery = recovery_with_transactions(context)
    with pytest.raises(asyncio.CancelledError):
        await recovery.recover()
    assert recovery.operations.db.tx.call_count == 1
    assert context.__aexit__.call_args.args[0] is asyncio.CancelledError
