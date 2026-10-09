"""Native Realtime shares admission, exact receipts, and bounded replay storage."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.billing.accounting.accounting_protocol import AccountingOutcome
from src.billing.accounting.accounting_service import AccountingProtocolService
from src.billing.accounting.accounting_snapshots import finalization_bytes
from src.billing.accounting.journal.accounting_turn_proofs import (
    AccountingTurnProofs,
    TURN_RESERVED_BYTES,
    ACKNOWLEDGED_BYTES,
)
from src.billing.charges.realtime_accounting_bounds import RealtimeCostBounds, realtime_cost_bounds
from src.billing.charges.realtime_native import NativeRealtimeBilling
from src.billing.charges.realtime_usage import RealtimeDurationUsage
from src.billing.spend.spend_operations import SpendPersistenceUnavailable
from src.realtime.errors import RealtimeError
from src.realtime.admission import RealtimeSessionPermit
from src.realtime.config import RealtimeSettings
from tests.realtime.test_pricing import charge_context, receipt
from tests.test_accounting_local_service import state
from tests.test_accounting_request_path import _AccountingRepository


@pytest.fixture(params=["assigned", "local"])
async def runtime(request):
    if request.param == "local":
        _, persistence, _, _, _, service = state(dwell_seconds=0)
        # The shared fixture defaults to completed replies. This suite also
        # checks uncertain receipts, so its reply must match the submitted outcome.
        persistence.change = lambda replies: [
            reply.model_copy(update={"outcome": value.finalization.outcome})
            for reply, value in zip(replies, persistence.calls[-1], strict=True)
        ]
    else:
        service = AccountingProtocolService(_AccountingRepository(), generation=7, dwell_seconds=0)
    service.start()
    identity = AsyncMock()
    billing = NativeRealtimeBilling(service, identity)
    context = replace(
        charge_context(), cost_bounds=RealtimeCostBounds(input_tokens=100, output_tokens=10)
    )
    try:
        yield billing, service, identity, context
    finally:
        await service.close()


async def dispatch(billing, context):
    operation = str(uuid4())
    await billing.dispatch(operation, context, expires_at=datetime.now(UTC) + timedelta(minutes=5))
    return operation


async def test_exact_receipt_uses_operation_identity_and_shared_terminal_owner(runtime):
    billing, service, identity, context = runtime
    await billing.check_owner(context)
    identity.check_owner.assert_awaited_once_with(context, budget_holds=True)
    original = service.finalize_operation
    service.finalize_operation = AsyncMock(wraps=original)
    operation = await dispatch(billing, context)
    usage = receipt(context.attribution.session_id)
    await asyncio.gather(*(billing.accept(operation, context, usage) for _ in range(4)))
    service.finalize_operation.assert_awaited_once()
    handle, terminal = service.finalize_operation.call_args.args
    assert handle.reservation.allowance == Decimal(".00034")
    assert terminal.outcome is AccountingOutcome.COMPLETED
    assert terminal.exact_charge == Decimal(".0000839")
    assert terminal.spend_payload["request_id"] == operation
    assert (
        terminal.spend_payload["metadata"]["realtime_session_id"] == context.attribution.session_id
    )
    assert terminal.spend_payload["metadata"]["realtime_receipt_id"] == usage.receipt_id
    assert handle.reservation.attribution.api_key == context.attribution.api_key
    assert billing.proofs.retained_bytes == ACKNOWLEDGED_BYTES
    await billing.close(context.attribution.session_id)
    assert billing.proofs.entries == billing.proofs.retained_bytes == 0


async def test_lost_terminal_reply_reuses_frozen_bytes_and_first_timestamps(runtime):
    billing, service, _, context = runtime
    operation = await dispatch(billing, context)
    original = service.finalize_operation
    attempts = []

    async def lose_reply(handle, terminal):
        attempts.append(finalization_bytes(terminal))
        reply = await original(handle, terminal)
        if len(attempts) == 1:
            raise TimeoutError()
        return reply

    service.finalize_operation = lose_reply
    usage = receipt(context.attribution.session_id)
    with pytest.raises(TimeoutError):
        await billing.accept(operation, context, usage)
    assert billing.proofs.retained_bytes == TURN_RESERVED_BYTES
    await billing.accept(operation, context, usage)
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    await billing.close(context.attribution.session_id)


async def test_cancelled_terminal_acceptance_is_replayed_by_owned_session_cleanup(runtime):
    billing, service, _, context = runtime
    operation = await dispatch(billing, context)
    original = service.finalize_operation
    reached = asyncio.Event()
    attempts = []

    async def wait_reply(handle, terminal):
        attempts.append(finalization_bytes(terminal))
        reply = await original(handle, terminal)
        if len(attempts) == 1:
            reached.set()
            await asyncio.Event().wait()
        return reply

    service.finalize_operation = wait_reply
    task = asyncio.create_task(
        billing.accept(operation, context, receipt(context.attribution.session_id))
    )
    try:
        await asyncio.wait_for(reached.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await billing.close(context.attribution.session_id)
        assert len(attempts) == 2 and attempts[0] == attempts[1]
        assert billing.proofs.entries == billing.proofs.retained_bytes == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_conflicting_receipt_or_owner_cannot_replace_an_accepted_charge(runtime):
    billing, service, _, context = runtime
    operation = await dispatch(billing, context)
    usage = receipt(context.attribution.session_id)
    await billing.accept(operation, context, usage)
    original = service.finalize_operation
    service.finalize_operation = AsyncMock(wraps=original)
    conflict = replace(usage, usage=replace(usage.usage, output_text=4))
    with pytest.raises(SpendPersistenceUnavailable):
        await billing.accept(operation, context, conflict)
    foreign = replace(context, attribution=replace(context.attribution, api_key="other-key"))
    with pytest.raises(SpendPersistenceUnavailable):
        await billing.accept(operation, foreign, usage)
    service.finalize_operation.assert_not_awaited()
    await billing.close(context.attribution.session_id)


@pytest.mark.parametrize("unknown", ["missing", "ceiling"])
async def test_unknown_or_excessive_usage_stays_provisional_and_is_not_forwarded(runtime, unknown):
    billing, service, _, context = runtime
    operation = await dispatch(billing, context)
    usage = receipt(context.attribution.session_id)
    usage = (
        replace(usage, usage=None, pending_reason="usage_missing")
        if unknown == "missing"
        else replace(usage, usage=replace(usage.usage, output_text=11))
    )
    original = service.finalize_operation
    service.finalize_operation = AsyncMock(wraps=original)
    with pytest.raises(RealtimeError, match="reconciliation"):
        await billing.accept(operation, context, usage)
    terminal = service.finalize_operation.call_args.args[1]
    assert terminal.outcome is AccountingOutcome.UNCERTAIN
    assert terminal.exact_charge is None and terminal.spend_payload is None
    assert terminal.uncertainty_reason == (
        "usage_missing" if unknown == "missing" else "usage_ceiling_exceeded"
    )
    with pytest.raises(RealtimeError, match="reconciliation"):
        await billing.accept(operation, context, usage)
    service.finalize_operation.assert_awaited_once()
    await billing.close(context.attribution.session_id)


async def test_disconnect_without_usage_uses_shared_conservative_finalization(runtime):
    billing, service, _, context = runtime
    operation = await dispatch(billing, context)
    original = service.finalize_operation
    service.finalize_operation = AsyncMock(wraps=original)
    await billing.close(context.attribution.session_id)
    terminal = service.finalize_operation.call_args.args[1]
    assert str(terminal.operation_id) == operation
    assert terminal.outcome is AccountingOutcome.UNCERTAIN
    assert terminal.uncertainty_reason == "terminal_usage_missing"
    assert billing.proofs.entries == 0


async def test_close_rejects_late_usage_and_double_cleanup_releases_storage_once(runtime):
    billing, service, _, context = runtime
    operation = await dispatch(billing, context)
    original = service.finalize_operation
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(handle, terminal):
        entered.set()
        await release.wait()
        return await original(handle, terminal)

    service.finalize_operation = AsyncMock(side_effect=blocked)
    first = asyncio.create_task(billing.close(context.attribution.session_id))
    tasks = [first]
    try:
        await asyncio.wait_for(entered.wait(), 1)
        late = asyncio.create_task(
            billing.accept(operation, context, receipt(context.attribution.session_id))
        )
        second = asyncio.create_task(billing.close(context.attribution.session_id))
        tasks.extend((late, second))
        release.set()
        await asyncio.gather(first, second)
        with pytest.raises(SpendPersistenceUnavailable):
            await late
        service.finalize_operation.assert_awaited_once()
        assert billing.proofs.entries == billing.proofs.retained_bytes == 0
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_wrong_terminal_reply_keeps_the_exact_proof_for_retry(runtime):
    billing, service, _, context = runtime
    operation = await dispatch(billing, context)
    original = service.finalize_operation
    attempts = []

    async def wrong_reply(handle, terminal):
        attempts.append(finalization_bytes(terminal))
        reply = await original(handle, terminal)
        return reply.model_copy(update={"operation_id": uuid4()}) if len(attempts) == 1 else reply

    service.finalize_operation = wrong_reply
    usage = receipt(context.attribution.session_id)
    with pytest.raises(SpendPersistenceUnavailable):
        await billing.accept(operation, context, usage)
    assert billing.proofs.retained_bytes == TURN_RESERVED_BYTES
    await billing.accept(operation, context, usage)
    assert attempts[0] == attempts[1]
    await billing.close(context.attribution.session_id)


@pytest.mark.parametrize("bound", ["entry", "bytes"])
async def test_replay_storage_overload_rejects_before_new_admission(runtime, bound):
    billing, service, _, context = runtime
    billing.proofs = AccountingTurnProofs(
        max_entries=1 if bound == "entry" else 2,
        max_bytes=TURN_RESERVED_BYTES if bound == "bytes" else 2 * TURN_RESERVED_BYTES,
    )
    await dispatch(billing, context)
    original = service.reserve
    service.reserve = AsyncMock(wraps=original)
    with pytest.raises(SpendPersistenceUnavailable):
        await dispatch(billing, context)
    service.reserve.assert_not_awaited()
    await billing.close(context.attribution.session_id)


async def test_unqualified_ceiling_denies_before_identity_or_provider_work(runtime):
    billing, _, identity, context = runtime
    with pytest.raises(RealtimeError, match="ceiling"):
        await billing.check_owner(replace(context, cost_bounds=None))
    identity.check_owner.assert_not_awaited()


async def test_long_session_turn_deadline_leaves_time_for_owned_cleanup(runtime):
    billing, _, _, context = runtime
    owner = SimpleNamespace(
        billing=billing,
        settings=RealtimeSettings(session_seconds=3600),
        keys=SimpleNamespace(get_auth_by_token_hash=AsyncMock()),
        require_ready=lambda: None,
    )
    request = SimpleNamespace(profile="realtime", auth=object())
    permit = RealtimeSessionPermit(
        owner, request, None, context, SimpleNamespace(turn=AsyncMock(return_value=None))
    )
    permit.check_health = AsyncMock()
    before = datetime.now(UTC)
    await permit.before_client_event({"type": "response.create"})
    assert before + timedelta(minutes=14) <= permit.turn_expires_at
    assert permit.turn_expires_at <= datetime.now(UTC) + timedelta(minutes=14)
    assert permit.turn_expires_at < permit.expires_at
    permit.turn_expires_at = datetime.now(UTC) + timedelta(seconds=owner.settings.cleanup_seconds)
    with pytest.raises(RealtimeError, match="accounting deadline"):
        await RealtimeSessionPermit.check_health(permit)
    owner.keys.get_auth_by_token_hash.assert_not_awaited()
    await billing.close(context.attribution.session_id)


def test_duration_bound_uses_bytes_not_wall_clock_and_rounds_up():
    limits = realtime_cost_bounds(
        {},
        transcription=True,
        duration=True,
        max_output_tokens=4096,
        max_input_bytes=48001,
    )
    assert limits.input_seconds >= Decimal(48001) / Decimal(48000)
    context = charge_context()
    assert limits.allowance(context.customer) == Decimal(".000100002083333334")
    assert limits.contains(RealtimeDurationUsage(Decimal(1)))
    assert not limits.contains(RealtimeDurationUsage(Decimal(2)))


@pytest.mark.parametrize("metadata", [{}, {"max_input_tokens": True}, {"max_input_tokens": 0}])
def test_token_profiles_need_positive_declared_context_ceiling(metadata):
    with pytest.raises(RealtimeError):
        realtime_cost_bounds(
            metadata,
            transcription=False,
            duration=False,
            max_output_tokens=4096,
            max_input_bytes=1024,
        )


def test_transcription_token_profile_needs_declared_provider_output_ceiling():
    with pytest.raises(RealtimeError):
        realtime_cost_bounds(
            {"max_input_tokens": 100},
            transcription=True,
            duration=False,
            max_output_tokens=4096,
            max_input_bytes=1024,
        )
