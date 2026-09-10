import asyncio
from collections import Counter
from unittest.mock import AsyncMock

import pytest

from src.batch.chat_capacity import ChatDeploymentCapacity
from src.models.errors import (
    FailureClassification,
    InvalidRequestError,
    ServiceUnavailableError,
    RoutingFailureAction,
)
from src.router.attempt_capacity import bind_attempt_capacity
from src.router.execution import RequestDeadline
from src.router import CooldownManager, FallbackConfig, FailoverManager, RedisStateBackend
from tests.batch.test_chat_capacity import deployment
from tests.test_failover import _planner


def manager_for(first, second=None, **fallbacks):
    state = RedisStateBackend(redis=None)
    registry = {"first": [first]}
    if second is not None:
        registry["second"] = [second]
    manager = FailoverManager(
        config=FallbackConfig(**fallbacks, num_retries=0, backoff_max=0),
        candidate_planner=_planner(state, registry),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    return manager, state


@pytest.mark.parametrize(
    "field,classification",
    [
        ("fallbacks", None),
        ("context_window_fallbacks", FailureClassification.CONTEXT_WINDOW),
        ("content_policy_fallbacks", FailureClassification.CONTENT_POLICY),
    ],
)
async def test_two_way_fallback_releases_previous_deployment_slots(field, classification):
    first, second = deployment(identity="first"), deployment(identity="second")
    manager, _ = manager_for(first, second, **{field: {"first": ["second"], "second": ["first"]}})
    capacity = ChatDeploymentCapacity(2)
    primary_calls = 0
    both_entered = asyncio.Event()
    active, peak = Counter(), Counter()

    async def item(primary):
        context = {}
        bind_attempt_capacity(context, capacity)
        first_attempt = True

        async def execute(target):
            nonlocal first_attempt, primary_calls
            key = target.deployment_id
            active[key] += 1
            peak[key] = max(peak[key], active[key])
            try:
                if first_attempt:
                    first_attempt = False
                    primary_calls += 1
                    if primary_calls == 2:
                        both_entered.set()
                    await both_entered.wait()
                    if classification is None:
                        raise ServiceUnavailableError(
                            affects_deployment_health=False,
                            routing_failure_action=RoutingFailureAction.NEXT_DEPLOYMENT,
                        )
                    raise InvalidRequestError(
                        failure_classification=classification, affects_deployment_health=False
                    )
                return key
            finally:
                active[key] -= 1

        return await manager.execute_with_failover(
            primary,
            primary.deployment_id,
            execute,
            routing_context=context,
        )

    result = await asyncio.wait_for(asyncio.gather(item(first), item(second)), 2)
    assert result == ["second", "first"]
    assert peak == {"first": 1, "second": 1} and capacity._total == 0


async def test_local_wait_timeout_does_not_acquire_provider_permit_or_damage_health(monkeypatch):
    target = deployment()
    manager, state = manager_for(target)
    capacity = ChatDeploymentCapacity(1)
    context = {}
    bind_attempt_capacity(context, capacity)
    provider = AsyncMock()
    acquire = AsyncMock(wraps=manager.candidate_planner.acquire_attempt)
    latency = AsyncMock()
    monkeypatch.setattr(manager.candidate_planner, "acquire_attempt", acquire)
    monkeypatch.setattr(manager, "_record_attempt_latency", latency)
    async with capacity.slot(target, RequestDeadline.after(1)):
        with pytest.raises(ServiceUnavailableError) as error:
            await manager.execute_with_failover(
                target,
                "first",
                provider,
                routing_context=context,
                timeout_seconds=0.02,
            )
    assert error.value.affects_deployment_health is False
    provider.assert_not_awaited()
    acquire.assert_not_awaited()
    latency.assert_not_awaited()
    assert not await state.is_cooled_down(target.health_ref)
    assert capacity._total == 0
