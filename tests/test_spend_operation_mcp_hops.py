from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from src.billing.spend.spend_operation_service import SpendOperationService
from src.billing.spend.spend_operations import (
    OperationAttempt,
    OperationHandle,
    OperationPrincipal,
    SpendOperationIntent,
)
from src.telemetry.spend_operation import settle_mcp_hop


def _handle(attempts: int) -> OperationHandle:
    attempt = OperationAttempt(
        deployment_id="dep-one", provider="openai", model="model-one", pricing={}
    )
    return OperationHandle(
        event_id=uuid4(),
        owner_token=uuid4(),
        intent=SpendOperationIntent(
            principal=OperationPrincipal(api_key="hashed-test-key"),
            model="model-one",
            call_type="completion",
            started_at=datetime.now(UTC),
            attempts=(attempt,) * attempts,
        ),
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )


async def _accepted_metadata(handle: OperationHandle) -> dict[str, object]:
    service = SpendOperationService(
        admission=MagicMock(), settlement=MagicMock(), worker=MagicMock()
    )
    service.settlement = SimpleNamespace(accept=AsyncMock())
    await service.accept(handle, {"metadata": {}})
    return service.settlement.accept.await_args.kwargs["payload"]["metadata"]


async def test_legacy_receipt_does_not_count_completed_mcp_hops_as_unresolved():
    request = SimpleNamespace(state=SimpleNamespace(spend_operation_handle=_handle(1)))
    settle_mcp_hop(request)
    handle = request.state.spend_operation_handle
    # The next hop adds its attempt; the first hop is now settled.
    handle = handle.model_copy(
        update={"intent": _handle(2).intent, "settled_attempts": handle.settled_attempts}
    )
    metadata = await _accepted_metadata(handle)
    assert metadata["unresolved_prior_attempts"] == 0


async def test_legacy_receipt_keeps_failed_retries_unresolved():
    metadata = await _accepted_metadata(_handle(3).model_copy(update={"settled_attempts": 1}))
    assert metadata["unresolved_prior_attempts"] == 1


def test_settling_without_an_operation_does_nothing():
    request = SimpleNamespace(state=SimpleNamespace())
    settle_mcp_hop(request)
    assert not hasattr(request.state, "spend_operation_handle")
