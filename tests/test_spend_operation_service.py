"""Background recovery stays bounded and never prevents receipt consumption."""

from unittest.mock import AsyncMock, patch

from src.billing.spend_operation_service import SpendOperationService


async def test_recovery_observes_unknown_backlog_at_most_once_per_ten_seconds():
    service = SpendOperationService(admission=None, settlement=None, worker=None)
    service.worker.recover_expired = AsyncMock(return_value=2)
    service.worker.unknown_count = AsyncMock(return_value=5)
    with patch("src.billing.spend_operation_service.observe_unknown_operations") as observe:
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
        patch("src.billing.spend_operation_service.observe_unknown_operations") as observe,
        patch("src.billing.spend_operation_service.increment_spend_ingestion_failure") as failure,
    ):
        await service.recover()
    service.worker.unknown_count.assert_not_awaited()
    observe.assert_not_called()
    failure.assert_called_once_with("operation_recovery")
