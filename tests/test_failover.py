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
