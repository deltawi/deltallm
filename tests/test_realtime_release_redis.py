"""Release acknowledgment and cleanup ownership with production router defaults."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.models.errors import ServiceUnavailableError
from src.router.candidates import AttemptCapacity
from tests import test_realtime_recovery_redis as fixtures

capacity_redis = fixtures.capacity_redis
pytestmark = [pytest.mark.integration, pytest.mark.redis]


async def session(dependencies, recovering):
    permit, state, cooldown, ref = await fixtures.recovery_session(dependencies)
    if not recovering:
        await cooldown.complete_recovery_attempt(permit.provider_permit)
        permit.provider_permit = await state.acquire_attempt(
            ref, AttemptCapacity(require_shared=True)
        )
        assert permit.provider_permit.acquired and not permit.provider_permit.recovery
    return permit, state, ref


@pytest.mark.parametrize("degraded_mode", ["fail_open", "fail_closed"])
@pytest.mark.parametrize(
    "recovering,status", [(False, "completed"), (True, "cancelled"), (True, "failed")]
)
async def test_release_outage_keeps_receipt_and_owner_for_cleanup(
    capacity_redis, monkeypatch, degraded_mode, recovering, status
):
    permit, state, ref = await session(capacity_redis, recovering)
    state.degraded_mode = degraded_mode
    attempt = permit.provider_permit
    original = state._redis_call
    monkeypatch.setattr(state, "_redis_call", AsyncMock(side_effect=ConnectionError("offline")))
    with pytest.raises(ServiceUnavailableError):
        await permit.accept_usage(fixtures.terminal(status=status))
    permit.owner.billing.accept.assert_awaited_once()
    assert permit.provider_permit is attempt
    monkeypatch.setattr(state, "_redis_call", original)
    assert await state.get_active_requests(ref.deployment_id) == 1
    await permit.close()
    assert permit.provider_permit is None
    assert await state.get_active_requests(ref.deployment_id) == 0
    next_attempt = await state.acquire_attempt(ref, AttemptCapacity(require_shared=True))
    assert next_attempt.acquired and next_attempt.recovery == recovering
    await state.release_attempt(next_attempt)


@pytest.mark.parametrize("recovering", [False, True])
async def test_cancellation_during_release_retains_cleanup_owner(
    capacity_redis, monkeypatch, recovering
):
    permit, state, ref = await session(capacity_redis, recovering)
    attempt = permit.provider_permit
    original = state._redis_call
    entered = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(state, "_redis_call", blocked)
    task = asyncio.create_task(permit.accept_usage(fixtures.terminal(status="cancelled")))
    try:
        await asyncio.wait_for(entered.wait(), 1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert permit.provider_permit is attempt
    monkeypatch.setattr(state, "_redis_call", original)
    await permit.close()
    assert permit.provider_permit is None
    assert await state.get_active_requests(ref.deployment_id) == 0


@pytest.mark.parametrize("recovering", [False, True])
async def test_lost_release_ack_cleanup_cannot_release_a_new_owner(
    capacity_redis, monkeypatch, recovering
):
    permit, state, ref = await session(capacity_redis, recovering)
    original = state._redis_call

    async def lost_ack(*args, **kwargs):
        await original(*args, **kwargs)
        raise ConnectionError("reply lost after commit")

    monkeypatch.setattr(state, "_redis_call", lost_ack)
    with pytest.raises(ServiceUnavailableError):
        await permit.accept_usage(fixtures.terminal(status="cancelled"))
    monkeypatch.setattr(state, "_redis_call", original)
    next_attempt = await state.acquire_attempt(ref, AttemptCapacity(require_shared=True))
    assert next_attempt.acquired and next_attempt.recovery == recovering
    await permit.close()
    assert permit.provider_permit is None
    assert await state.get_active_requests(ref.deployment_id) == 1
    if recovering:
        assert await capacity_redis[0].get(state._recovery_key(ref)) == next_attempt.owner_token
    await state.release_attempt(next_attempt)


@pytest.mark.parametrize("failure", [RuntimeError("dispatch failed"), asyncio.CancelledError()])
async def test_failed_dispatch_retains_owner_when_release_is_unconfirmed(
    capacity_redis, monkeypatch, failure
):
    permit, state, ref = await session(capacity_redis, True)
    attempt = permit.provider_permit
    permit.provider_permit = permit.current = None
    permit.request.auth = object()
    permit.leases = SimpleNamespace(turn=AsyncMock(return_value=attempt))
    permit.owner.billing.dispatch = AsyncMock(side_effect=failure)
    monkeypatch.setattr(permit, "check_health", AsyncMock())
    original = state._redis_call
    monkeypatch.setattr(state, "_redis_call", AsyncMock(side_effect=ConnectionError("offline")))
    with pytest.raises(ServiceUnavailableError):
        await permit.before_client_event({"type": "response.create"})
    assert permit.provider_permit is attempt
    assert permit.current is None and permit.turns == 0
    monkeypatch.setattr(state, "_redis_call", original)
    await permit.close()
    assert permit.provider_permit is None
    assert await state.get_active_requests(ref.deployment_id) == 0


@pytest.mark.parametrize("recovering", [False, True])
async def test_confirmed_zero_release_succeeds_once(capacity_redis, monkeypatch, recovering):
    permit, state, ref = await session(capacity_redis, recovering)
    calls = AsyncMock(wraps=state._redis_call)
    monkeypatch.setattr(state, "_redis_call", calls)
    await permit.accept_usage(fixtures.terminal(status="cancelled"))
    await permit.close()
    assert calls.await_count == 1
    assert permit.provider_permit is None
    assert await state.get_active_requests(ref.deployment_id) == 0
