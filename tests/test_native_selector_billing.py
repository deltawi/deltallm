"""Selector components share exact admission and terminal replay in both modes."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from src.billing.accounting.accounting_protocol import AccountingOutcome
from src.billing.accounting.accounting_service import AccountingProtocolService
from src.billing.accounting.accounting_snapshots import finalization_bytes
from src.billing.charges.operation_reservation import (
    BillingOperationUnavailable,
    ComponentState,
    SoftSelectorOperation,
    token_price_allowance,
)
from src.billing.charges.selector_native import NativeSelectorBilling
from tests.test_accounting_local_service import state
from tests.test_accounting_request_path import _AccountingRepository
from tests.test_selector_charge import make_selector_charge


@pytest.fixture(params=["assigned", "local"])
async def native(request):
    if request.param == "local":
        _, persistence, _, _, _, service = state(dwell_seconds=0)
        persistence.change = lambda replies: [
            reply.model_copy(update={"outcome": value.finalization.outcome})
            for reply, value in zip(replies, persistence.calls[-1], strict=True)
        ]
    else:
        service = AccountingProtocolService(_AccountingRepository(), generation=7, dwell_seconds=0)
    service.start()
    charge = make_selector_charge()
    operation = SoftSelectorOperation(
        attribution=charge.attribution,
        owner_token=uuid4(),
        pricing=charge.pricing,
        admission_allowance=token_price_allowance(
            charge.pricing, input_tokens=1000, output_tokens=64
        ),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    store = NativeSelectorBilling(service).bind(operation, max_input_tokens=1000)
    try:
        yield store, service, operation, charge
    finally:
        await store.close()
        await service.close()


def deadline():
    return asyncio.get_running_loop().time() + 1


async def admitted(native, dispatched=True):
    store, _, operation, _ = native
    result = await store.reserve(operation, expires_at=deadline())
    assert result.selector_state is ComponentState.RESERVED
    assert result.answer_state is ComponentState.UNATTEMPTED
    if dispatched:
        await store.dispatch(operation, component="selector", expires_at=deadline())


async def test_native_selector_preserves_component_and_parent_identities_and_exact_charge(native):
    store, service, operation, charge = native
    await admitted(native)
    service.finalize_operation = AsyncMock(wraps=service.finalize_operation)
    await asyncio.gather(
        *(store.accept_selector(operation, charge, expires_at=deadline()) for _ in range(3))
    )
    service.finalize_operation.assert_awaited_once()
    handle, terminal = service.finalize_operation.call_args.args
    assert handle.reservation.operation_id == UUID(operation.attribution.component_event_id)
    assert handle.reservation.operation_id != operation.attribution.operation_id
    assert terminal.event_id == handle.reservation.operation_id
    assert terminal.exact_charge == charge.customer_charge
    assert terminal.spend_payload["request_id"] == str(handle.reservation.operation_id)
    assert terminal.spend_payload["metadata"]["parent_event_id"] == str(
        operation.attribution.operation_id
    )
    assert terminal.spend_payload["provider_cost_exact"] == str(charge.provider_cost)
    await store.close()
    service.finalize_operation.assert_awaited_once()


async def test_lost_selector_reply_replays_identical_frozen_bytes_without_provider_work(native):
    store, service, operation, charge = native
    await admitted(native)
    original, attempts = service.finalize_operation, []

    async def lose(handle, terminal):
        attempts.append(finalization_bytes(terminal))
        reply = await original(handle, terminal)
        if len(attempts) == 1:
            raise TimeoutError()
        return reply

    service.finalize_operation = lose
    with pytest.raises(BillingOperationUnavailable):
        await store.accept_selector(operation, charge, expires_at=deadline())
    await store.close()
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert store.acknowledged


@pytest.mark.parametrize("dispatched", [False, True])
async def test_selector_cleanup_distinguishes_unsent_from_unknown_work(native, dispatched):
    store, service, _, _ = native
    await admitted(native, dispatched)
    service.finalize_operation = AsyncMock(wraps=service.finalize_operation)
    await store.close()
    terminal = service.finalize_operation.call_args.args[1]
    assert terminal.outcome is (
        AccountingOutcome.UNCERTAIN if dispatched else AccountingOutcome.NOT_DISPATCHED
    )
    assert terminal.exact_charge is None and terminal.spend_payload is None


async def test_proved_pre_send_rejection_returns_the_full_native_allowance(native):
    store, service, operation, _ = native
    await admitted(native)
    service.finalize_operation = AsyncMock(wraps=service.finalize_operation)
    await store.confirm_not_dispatched(operation, expires_at=deadline())
    assert service.finalize_operation.call_args.args[1].outcome is AccountingOutcome.NOT_DISPATCHED
    await store.close()
    service.finalize_operation.assert_awaited_once()


async def test_provider_ceiling_violation_keeps_full_provisional_debit(native):
    store, service, operation, charge = native
    await admitted(native)
    service.finalize_operation = AsyncMock(wraps=service.finalize_operation)
    usage = charge.usage.model_copy(update={"prompt_tokens": 1001, "total_tokens": 1006})
    excessive = charge.model_copy(update={"usage": usage})
    with pytest.raises(BillingOperationUnavailable):
        await store.accept_selector(operation, excessive, expires_at=deadline())
    terminal = service.finalize_operation.call_args.args[1]
    assert terminal.outcome is AccountingOutcome.UNCERTAIN
    assert terminal.uncertainty_reason == "selector_ceiling_exceeded"
    await store.close()
    service.finalize_operation.assert_awaited_once()


async def test_foreign_owner_conflicting_receipt_and_duplicate_dispatch_are_denied(native):
    store, service, operation, charge = native
    await admitted(native)
    with pytest.raises(BillingOperationUnavailable):
        await store.dispatch(operation, component="selector", expires_at=deadline())
    with pytest.raises(BillingOperationUnavailable):
        await store.accept_selector(
            operation.model_copy(update={"owner_token": uuid4()}), charge, expires_at=deadline()
        )
    await store.accept_selector(operation, charge, expires_at=deadline())
    service.finalize_operation = AsyncMock(wraps=service.finalize_operation)
    with pytest.raises(BillingOperationUnavailable):
        await store.accept_selector(
            operation,
            charge.model_copy(update={"finished_at": charge.finished_at + timedelta(seconds=1)}),
            expires_at=deadline(),
        )
    service.finalize_operation.assert_not_awaited()


async def test_underquoted_selector_contract_fails_before_admission(native):
    _, service, operation, _ = native
    service.reserve = AsyncMock(wraps=service.reserve)
    with pytest.raises(BillingOperationUnavailable):
        NativeSelectorBilling(service).bind(
            operation.model_copy(update={"admission_allowance": Decimal(0)}), max_input_tokens=1000
        )
    service.reserve.assert_not_awaited()
