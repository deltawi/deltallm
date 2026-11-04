"""Background recovery stays bounded and never prevents receipt consumption."""

from unittest.mock import AsyncMock, patch

from src.billing.spend.spend_operation_service import SpendOperationService


async def test_recovery_observes_unknown_backlog_at_most_once_per_ten_seconds():
    service = SpendOperationService(admission=None, settlement=None, worker=None)
    service.worker.recover_expired = AsyncMock(return_value=2)
    service.worker.unknown_count = AsyncMock(return_value=5)
    with patch("src.billing.spend.spend_operation_service.observe_unknown_operations") as observe:
        await service.recover()
        await service.recover()
    assert service.worker.recover_expired.await_count == 2
    service.worker.unknown_count.assert_awaited_once()
    observe.assert_called_once_with(5)


async def test_recovery_failure_preserves_last_observation_and_returns_to_worker():
    service = SpendOperationService(admission=None, settlement=None, worker=None)
    service.worker.recover_expired = AsyncMock(side_effect=TimeoutError)
    service.worker.unknown_count = AsyncMock()
    with (
        patch("src.billing.spend.spend_operation_service.observe_unknown_operations") as observe,
        patch(
            "src.billing.spend.spend_operation_service.increment_spend_ingestion_failure"
        ) as failure,
    ):
        await service.recover()
    service.worker.unknown_count.assert_not_awaited()
    observe.assert_not_called()
    failure.assert_called_once_with("operation_recovery")


def receipt_handle():
    from types import SimpleNamespace
    from uuid import uuid4

    intent = SimpleNamespace(model_dump=lambda mode="json": {"attempts": []})
    return SimpleNamespace(
        event_id=uuid4(),
        owner_token=uuid4(),
        intent=intent,
        unresolved_prior_attempts=lambda: 0,
    )


async def test_a_slow_receipt_is_written_again_and_accepted():
    from src.billing.charges.operation_reservation import BillingOperationUnavailable

    service = SpendOperationService(admission=None, settlement=None, worker=None)
    service.settlement.accept = AsyncMock(side_effect=[BillingOperationUnavailable(), None])
    with patch(
        "src.billing.spend.spend_operation_service.increment_spend_ingestion_failure"
    ) as failure:
        await service.accept(receipt_handle(), {"model": "m"})
    assert service.settlement.accept.await_count == 2
    first, second = service.settlement.accept.await_args_list
    assert first.kwargs["payload"] == second.kwargs["payload"]
    assert first.kwargs["event_id"] == second.kwargs["event_id"]
    failure.assert_called_once_with("operation_receipt_retry")


async def test_receipt_attempts_are_bounded():
    import pytest

    from src.billing.charges.operation_reservation import BillingOperationUnavailable
    from src.billing.spend.spend_operation_service import RECEIPT_ATTEMPTS
    from src.billing.spend.spend_operations import SpendPersistenceUnavailable

    service = SpendOperationService(admission=None, settlement=None, worker=None)
    service.settlement.accept = AsyncMock(side_effect=BillingOperationUnavailable())
    with pytest.raises(SpendPersistenceUnavailable):
        await service.accept(receipt_handle(), {"model": "m"})
    assert service.settlement.accept.await_count == RECEIPT_ATTEMPTS


async def test_a_rejected_receipt_is_not_written_again():
    import pytest

    from src.billing.spend.spend_operations import SpendPersistenceUnavailable
    from src.db.billing.spend_operations import ReceiptRejected

    service = SpendOperationService(admission=None, settlement=None, worker=None)
    service.settlement.accept = AsyncMock(side_effect=ReceiptRejected())
    with pytest.raises(SpendPersistenceUnavailable):
        await service.accept(receipt_handle(), {"model": "m"})
    assert service.settlement.accept.await_count == 1
