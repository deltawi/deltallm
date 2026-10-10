import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.errors import ServiceUnavailableError
from src.realtime.admission import RealtimeSessionPermit
from src.realtime.config import RealtimeSettings
from src.router.candidates import AttemptCapacity
from src.router.cooldown import CooldownManager
from src.router.health_state import DeploymentHealthRef
from src.router.state import RedisStateBackend
from tests import test_selector_capacity_redis as fixtures
from tests.realtime.test_pricing import charge_context
from tests.realtime.test_usage import usage

capacity_redis = fixtures.capacity_redis
pytestmark = [pytest.mark.integration, pytest.mark.redis]


async def recovery_session(dependencies, profile="realtime"):
    first, _, identity = dependencies
    state = RedisStateBackend(first)
    ref = DeploymentHealthRef(identity)
    cooldown = CooldownManager(state)
    await cooldown.manual_cooldown(ref, 60)
    # Advance just the cooldown, leaving the recovery-required state intact.
    await first.delete(state.keyspace.cooldown(identity, ref.generation))
    attempt = await state.acquire_attempt(ref, AttemptCapacity(require_shared=True))
    assert attempt.acquired and attempt.recovery
    context = charge_context()
    owner = SimpleNamespace(
        settings=RealtimeSettings(),
        billing=SimpleNamespace(accept=AsyncMock(), terminal_lifetime=None),
    )
    request = SimpleNamespace(session_id=context.attribution.session_id, profile=profile)
    route = SimpleNamespace(
        usage_type="tokens",
        generation=SimpleNamespace(router=SimpleNamespace(state=state), cooldown_manager=cooldown),
    )
    permit = RealtimeSessionPermit(owner, request, route, context, None)
    permit.current = str(uuid4())
    permit.provider_permit = attempt
    return permit, state, cooldown, ref


def terminal(profile="realtime", status="completed"):
    if profile == "transcription":
        return {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item1",
            "content_index": 0,
            "usage": {
                "type": "tokens",
                "input_tokens": 17,
                "output_tokens": 9,
                "input_token_details": {"text_tokens": 0, "audio_tokens": 17},
            },
        }
    return {
        "type": "response.done",
        "response": {"id": "response1", "status": status, "usage": usage()},
    }


@pytest.mark.parametrize("profile", ["realtime", "transcription"])
async def test_successful_recovery_restores_concurrency_in_one_call(
    capacity_redis, monkeypatch, profile
):
    permit, state, _, ref = await recovery_session(capacity_redis, profile)
    calls = AsyncMock(wraps=state._redis_call)
    monkeypatch.setattr(state, "_redis_call", calls)
    await permit.accept_usage(terminal(profile))
    assert calls.await_count == 1
    assert (await state.get_health(ref))["healthy"] == "true"
    assert (await state.get_health(ref))["recovery_required"] == "false"
    assert await state.get_active_requests(ref.deployment_id) == 0
    assert permit.provider_permit is None
    other = RedisStateBackend(capacity_redis[1], degraded_mode="fail_closed")
    attempts = await asyncio.gather(
        *(
            other.acquire_attempt(ref, AttemptCapacity(max_concurrency=2, require_shared=True))
            for _ in range(5)
        )
    )
    assert sum(attempt.acquired for attempt in attempts) == 2
    assert not any(attempt.recovery for attempt in attempts)


@pytest.mark.parametrize("status", ["cancelled", "failed", "incomplete", None])
async def test_unsuccessful_completion_only_releases_its_attempt(capacity_redis, status):
    permit, state, _, ref = await recovery_session(capacity_redis)
    await permit.accept_usage(terminal(status=status))
    assert (await state.get_health(ref))["healthy"] == "false"
    assert await state.get_active_requests(ref.deployment_id) == 0


async def test_duplicate_receipt_cannot_release_the_next_turn(capacity_redis):
    permit, state, _, ref = await recovery_session(capacity_redis)
    event = terminal()
    await permit.accept_usage(event)
    next_attempt = await state.acquire_attempt(ref, AttemptCapacity(require_shared=True))
    permit.current = str(uuid4())
    permit.provider_permit = next_attempt
    await permit.accept_usage(event)
    assert permit.provider_permit is next_attempt
    assert await state.get_active_requests(ref.deployment_id) == 1
    await permit.close()


async def test_recovery_cannot_override_a_new_manual_cooldown(capacity_redis):
    permit, state, cooldown, ref = await recovery_session(capacity_redis)
    await cooldown.manual_cooldown(ref, 60)
    await permit.accept_usage(terminal())
    assert await state.is_cooled_down(ref)
    assert (await state.get_health(ref))["healthy"] == "false"
    assert await state.get_active_requests(ref.deployment_id) == 0


@pytest.mark.parametrize("loss", ["token", "expiry", "generation"])
async def test_stale_recovery_cannot_change_current_health(capacity_redis, loss):
    permit, state, _, ref = await recovery_session(capacity_redis)
    attempt = permit.provider_permit
    if loss == "token":
        await capacity_redis[0].set(state._recovery_key(ref), "replacement-owner", ex=60)
    elif loss == "expiry":
        await capacity_redis[0].zadd(
            state.keyspace.attempt_owners(ref.deployment_id), {attempt.owner_token: 0}
        )
    else:
        new_ref = replace(ref, generation="replacement-generation")
        await state.apply_manual_cooldown(new_ref, 60, "replacement")
    await permit.accept_usage(terminal())
    if loss == "generation":
        assert (await state.get_health(new_ref))["healthy"] == "false"
    else:
        assert (await state.get_health(ref))["healthy"] == "false"
    assert await state.get_active_requests(ref.deployment_id) == 0


async def test_recovery_failure_keeps_receipt_and_cleanup_owner(capacity_redis, monkeypatch):
    permit, state, _, ref = await recovery_session(capacity_redis)
    attempt = permit.provider_permit
    original = state._redis_call
    monkeypatch.setattr(state, "_redis_call", AsyncMock(side_effect=ConnectionError("offline")))
    with pytest.raises(ServiceUnavailableError):
        await permit.accept_usage(terminal())
    permit.owner.billing.accept.assert_awaited_once()
    assert permit.provider_permit is attempt
    monkeypatch.setattr(state, "_redis_call", original)
    await permit.close()
    assert await state.get_active_requests(ref.deployment_id) == 0


async def test_cancelled_completion_retains_cleanup_owner(capacity_redis, monkeypatch):
    permit, state, _, ref = await recovery_session(capacity_redis)
    original = state._redis_call
    entered = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(state, "_redis_call", blocked)
    task = asyncio.create_task(permit.accept_usage(terminal()))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    permit.owner.billing.accept.assert_awaited_once()
    monkeypatch.setattr(state, "_redis_call", original)
    await permit.close()
    assert await state.get_active_requests(ref.deployment_id) == 0


async def test_lost_completion_ack_cannot_release_a_new_owner(capacity_redis, monkeypatch):
    permit, state, _, ref = await recovery_session(capacity_redis)
    original = state._redis_call

    async def lost_ack(*args, **kwargs):
        await original(*args, **kwargs)
        raise ConnectionError("reply lost after commit")

    monkeypatch.setattr(state, "_redis_call", lost_ack)
    with pytest.raises(ServiceUnavailableError):
        await permit.accept_usage(terminal())
    permit.owner.billing.accept.assert_awaited_once()
    monkeypatch.setattr(state, "_redis_call", original)
    next_attempt = await state.acquire_attempt(ref, AttemptCapacity(require_shared=True))
    assert next_attempt.acquired and not next_attempt.recovery
    await permit.close()
    assert await state.get_active_requests(ref.deployment_id) == 1
    assert (await state.get_health(ref))["healthy"] == "true"
    await state.release_attempt(next_attempt)
