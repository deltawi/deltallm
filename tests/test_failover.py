from __future__ import annotations

import asyncio

import pytest

from src.models.errors import TimeoutError
from src.router import CooldownManager, FallbackConfig, FailoverManager, RedisStateBackend
from src.router.router import Deployment


def _deployment(deployment_id: str) -> Deployment:
    return Deployment(
        deployment_id=deployment_id,
        model_name="gpt-4o-mini",
        deltallm_params={"model": "openai/gpt-4o-mini"},
    )


@pytest.mark.asyncio
async def test_failover_applies_retry_override():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        deployment_registry={"group-a": [primary]},
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    attempts = {"count": 0}

    async def run(_deployment: Deployment) -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TimeoutError(message="slow upstream")
        return "ok"

    data, served = await manager.execute_with_failover(
        primary_deployment=primary,
        model_group="group-a",
        execute=run,
        return_deployment=True,
        retry_max_attempts=1,
        retryable_error_classes=["timeout"],
    )

    assert data == "ok"
    assert served.deployment_id == "dep-a"
    assert attempts["count"] == 2


@pytest.mark.asyncio
async def test_failover_applies_timeout_override():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        deployment_registry={"group-a": [primary]},
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )

    async def run(_deployment: Deployment) -> str:
        await asyncio.sleep(0.05)
        return "slow-ok"

    with pytest.raises(TimeoutError):
        await manager.execute_with_failover(
            primary_deployment=primary,
            model_group="group-a",
            execute=run,
            timeout_seconds=0.01,
            retry_max_attempts=0,
        )


def test_failover_event_history_is_bounded_and_preserves_recent_order():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(event_history_size=3),
        deployment_registry={"group-a": [primary]},
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )

    manager._record_fallback_event("group-a", "dep-1", "dep-2", "retry", "timeout", "one", 1, False)
    manager._record_fallback_event("group-a", "dep-2", "dep-3", "retry", "timeout", "two", 2, False)
    manager._record_fallback_event("group-a", "dep-3", "dep-4", "retry", "timeout", "three", 3, False)
    manager._record_fallback_event("group-a", "dep-4", "dep-5", "retry", "timeout", "four", 4, True)

    events = manager.get_recent_fallback_events(limit=10)

    assert len(events) == 3
    assert [event["from_deployment"] for event in events] == ["dep-2", "dep-3", "dep-4"]
    assert events[-1]["success"] is True


def test_failover_event_history_limit_returns_tail_subset():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(event_history_size=5),
        deployment_registry={"group-a": [primary]},
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )

    for attempt in range(1, 5):
        manager._record_fallback_event(
            "group-a",
            f"dep-{attempt}",
            f"dep-{attempt + 1}",
            "retry",
            "timeout",
            f"event-{attempt}",
            attempt,
            False,
        )

    events = manager.get_recent_fallback_events(limit=2)

    assert len(events) == 2
    assert [event["attempt"] for event in events] == [3, 4]
