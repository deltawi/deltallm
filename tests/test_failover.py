from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from src.models.errors import (
    GatewayCapacityError,
    InvalidRequestError,
    NO_HEALTHY_DEPLOYMENTS_CODE,
    RateLimitError,
    ServiceUnavailableError,
    TimeoutError,
    parse_retry_after_header,
)
from src.providers.healthcheck import HealthProbeResult, probe_provider_health
from src.router import (
    BackgroundHealthChecker,
    CooldownManager,
    FallbackConfig,
    FailoverManager,
    HealthCheckConfig,
    PassiveHealthTracker,
    RedisStateBackend,
    ROUTING_MODE_CONTEXT_KEY,
    RouteGroupPolicy,
    Router,
    RouterConfig,
    RoutingStrategy,
)
from src.router.health_policy import affects_deployment_health
from src.router.router import Deployment


def _deployment(
    deployment_id: str,
    *,
    mode: str = "chat",
    tags: list[str] | None = None,
    priority: int = 0,
    rpm_limit: int | None = None,
    provider: str = "openai",
) -> Deployment:
    return Deployment(
        deployment_id=deployment_id,
        model_name="gpt-4o-mini",
        deltallm_params={
            "provider": provider,
            "model": f"{provider}/gpt-4o-mini",
        },
        model_info={"mode": mode},
        tags=list(tags or []),
        priority=priority,
        rpm_limit=rpm_limit,
    )


def _planner(
    state: RedisStateBackend,
    registry: dict[str, list[Deployment]],
    *,
    config: RouterConfig | None = None,
    strategy: RoutingStrategy = RoutingStrategy.USAGE_BASED,
) -> Router:
    return Router(
        strategy=strategy,
        state_backend=state,
        config=config or RouterConfig(),
        deployment_registry=registry,
    )


@pytest.mark.asyncio
async def test_failover_applies_retry_override():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary]}),
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
        candidate_planner=_planner(state, {"group-a": [primary]}),
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


@pytest.mark.asyncio
async def test_failover_applies_timeout_resolver_per_deployment():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    fallback = _deployment("dep-b")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary, fallback]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        if deployment.deployment_id == "dep-a":
            await asyncio.sleep(0.05)
            return "late-primary"
        return "fallback-ok"

    def timeout_for_deployment(deployment: Deployment) -> float:
        return 0.01 if deployment.deployment_id == "dep-a" else 1.0

    data, served = await manager.execute_with_failover(
        primary_deployment=primary,
        model_group="group-a",
        execute=run,
        return_deployment=True,
        timeout_for_deployment=timeout_for_deployment,
    )

    assert data == "fallback-ok"
    assert served.deployment_id == "dep-b"
    assert attempts == ["dep-a", "dep-b"]


@pytest.mark.parametrize(
    "mode",
    [
        "chat",
        "embedding",
        "image_generation",
        "audio_speech",
        "audio_transcription",
        "rerank",
    ],
)
@pytest.mark.asyncio
async def test_failover_never_attempts_a_different_workload_mode(mode: str):
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-primary", mode=mode, priority=0, provider="vllm")
    wrong_mode = "embedding" if mode != "embedding" else "chat"
    incompatible = _deployment(
        "dep-incompatible",
        mode=wrong_mode,
        priority=1,
        provider="vllm",
    )
    fallback = _deployment("dep-fallback", mode=mode, priority=2, provider="vllm")
    registry = {"group-a": [primary, incompatible, fallback]}
    planner = _planner(state, registry, strategy=RoutingStrategy.PRIORITY_BASED)
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=planner,
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    routing_context = {ROUTING_MODE_CONTEXT_KEY: mode, "metadata": {}}
    selected = await planner.select_deployment("group-a", routing_context)
    assert selected is primary
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        if deployment.deployment_id == primary.deployment_id:
            raise TimeoutError(message="primary timed out")
        return "ok"

    data, served = await manager.execute_with_failover(
        primary_deployment=primary,
        model_group="group-a",
        execute=run,
        return_deployment=True,
        routing_context=routing_context,
    )

    assert data == "ok"
    assert served is fallback
    assert attempts == ["dep-primary", "dep-fallback"]


@pytest.mark.parametrize(
    ("mode", "incompatible_provider"),
    [
        ("chat", "elevenlabs"),
        ("embedding", "anthropic"),
        ("image_generation", "anthropic"),
        ("audio_speech", "anthropic"),
        ("audio_transcription", "anthropic"),
        ("rerank", "anthropic"),
    ],
)
@pytest.mark.asyncio
async def test_failover_never_attempts_a_provider_without_workload_capability(
    mode: str,
    incompatible_provider: str,
):
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-primary", mode=mode, priority=0, provider="vllm")
    incompatible = _deployment(
        "dep-incompatible",
        mode=mode,
        priority=1,
        provider=incompatible_provider,
    )
    fallback = _deployment("dep-fallback", mode=mode, priority=2, provider="vllm")
    registry = {"group-a": [primary, incompatible, fallback]}
    planner = _planner(state, registry, strategy=RoutingStrategy.PRIORITY_BASED)
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=planner,
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    routing_context = {ROUTING_MODE_CONTEXT_KEY: mode, "metadata": {}}
    selected = await planner.select_deployment("group-a", routing_context)
    assert selected is primary
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        if deployment is primary:
            raise TimeoutError(message="primary timed out")
        return "ok"

    data, served = await manager.execute_with_failover(
        primary_deployment=primary,
        model_group="group-a",
        execute=run,
        return_deployment=True,
        routing_context=routing_context,
    )

    assert data == "ok"
    assert served is fallback
    assert attempts == ["dep-primary", "dep-fallback"]


@pytest.mark.asyncio
async def test_general_fallback_group_reuses_all_router_eligibility_checks():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-primary", tags=["vip"], priority=0)
    wrong_tag = _deployment("dep-wrong-tag", tags=["standard"], priority=0)
    unhealthy = _deployment("dep-unhealthy", tags=["vip"], priority=1)
    cooled = _deployment("dep-cooled", tags=["vip"], priority=2)
    at_capacity = _deployment(
        "dep-at-capacity",
        tags=["vip"],
        priority=3,
        rpm_limit=1,
    )
    eligible = _deployment("dep-eligible", tags=["vip"], priority=4)
    registry = {
        "group-a": [primary],
        "fallback-group": [wrong_tag, unhealthy, cooled, at_capacity, eligible],
    }
    await state.set_health(unhealthy.deployment_id, False)
    await state.set_cooldown(cooled.deployment_id, 30, "manual")
    await state.increment_usage(at_capacity.deployment_id, 0)
    planner = _planner(
        state,
        registry,
        config=RouterConfig(
            enable_pre_call_checks=True,
            route_group_policies={
                "fallback-group": RouteGroupPolicy(
                    strategy=RoutingStrategy.PRIORITY_BASED,
                )
            },
        ),
    )
    manager = FailoverManager(
        config=FallbackConfig(
            num_retries=0,
            timeout=1.0,
            fallbacks={"group-a": ["fallback-group"]},
        ),
        candidate_planner=planner,
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    routing_context = {
        ROUTING_MODE_CONTEXT_KEY: "chat",
        "metadata": {"tags": ["vip"]},
    }
    selected = await planner.select_deployment("group-a", routing_context)
    assert selected is primary
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        if deployment is primary:
            raise TimeoutError(message="primary timed out")
        return "fallback-ok"

    data, served = await manager.execute_with_failover(
        primary_deployment=primary,
        model_group="group-a",
        execute=run,
        return_deployment=True,
        routing_context=routing_context,
    )

    assert data == "fallback-ok"
    assert served is eligible
    assert attempts == ["dep-primary", "dep-eligible"]


@pytest.mark.asyncio
async def test_classified_fallback_group_reuses_request_tags_and_policy_order():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-primary", tags=["vip"], priority=0)
    wrong_tag = _deployment("dep-wrong-tag", tags=["standard"], priority=0)
    eligible = _deployment("dep-eligible", tags=["vip"], priority=1)
    registry = {
        "group-a": [primary],
        "context-group": [wrong_tag, eligible],
    }
    planner = _planner(state, registry, strategy=RoutingStrategy.PRIORITY_BASED)
    manager = FailoverManager(
        config=FallbackConfig(
            num_retries=0,
            timeout=1.0,
            context_window_fallbacks={"group-a": ["context-group"]},
        ),
        candidate_planner=planner,
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    routing_context = {
        ROUTING_MODE_CONTEXT_KEY: "chat",
        "metadata": {"tags": ["vip"]},
    }
    selected = await planner.select_deployment("group-a", routing_context)
    assert selected is primary
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        if deployment is primary:
            raise InvalidRequestError(message="maximum context length reached")
        return "fallback-ok"

    data, served = await manager.execute_with_failover(
        primary_deployment=primary,
        model_group="group-a",
        execute=run,
        return_deployment=True,
        routing_context=routing_context,
    )

    assert data == "fallback-ok"
    assert served is eligible
    assert attempts == ["dep-primary", "dep-eligible"]


@pytest.mark.asyncio
async def test_failover_consumes_cached_plan_without_per_candidate_state_reads():
    class CountingState(RedisStateBackend):
        def __init__(self) -> None:
            super().__init__(redis=None)
            self.health_batch_calls = 0
            self.cooldown_batch_calls = 0
            self.health_calls = 0
            self.cooldown_calls = 0
            self.attempt_acquisition_calls = 0
            self.attempt_release_calls = 0

        async def get_health_batch(self, deployment_ids):  # noqa: ANN001, ANN201
            self.health_batch_calls += 1
            return await super().get_health_batch(deployment_ids)

        async def get_cooldown_batch(self, deployment_ids):  # noqa: ANN001, ANN201
            self.cooldown_batch_calls += 1
            return await super().get_cooldown_batch(deployment_ids)

        async def get_health(self, deployment_id):  # noqa: ANN001, ANN201
            self.health_calls += 1
            return await super().get_health(deployment_id)

        async def is_cooled_down(self, deployment_id):  # noqa: ANN001, ANN201
            self.cooldown_calls += 1
            return await super().is_cooled_down(deployment_id)

        async def acquire_attempt(  # noqa: ANN001, ANN201
            self, deployment_id, capacity, *, lease_ttl_seconds=630
        ):
            self.attempt_acquisition_calls += 1
            return await super().acquire_attempt(
                deployment_id,
                capacity,
                lease_ttl_seconds=lease_ttl_seconds,
            )

        async def release_attempt(self, permit):  # noqa: ANN001, ANN201
            self.attempt_release_calls += 1
            return await super().release_attempt(permit)

    state = CountingState()
    primary = _deployment("dep-primary", priority=0)
    fallback = _deployment("dep-fallback", priority=1)
    planner = _planner(
        state,
        {"group-a": [primary, fallback]},
        strategy=RoutingStrategy.PRIORITY_BASED,
    )
    manager = FailoverManager(
        config=FallbackConfig(),
        candidate_planner=planner,
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    routing_context = {ROUTING_MODE_CONTEXT_KEY: "chat", "metadata": {}}
    selected = await planner.select_deployment("group-a", routing_context)
    assert selected is primary

    result = await manager.execute_with_failover(
        primary_deployment=primary,
        model_group="group-a",
        execute=lambda deployment: asyncio.sleep(0, result=deployment.deployment_id),
        routing_context=routing_context,
    )

    assert result == "dep-primary"
    assert state.health_batch_calls == 1
    assert state.cooldown_batch_calls == 1
    assert state.health_calls == 0
    assert state.cooldown_calls == 0
    assert state.attempt_acquisition_calls == 1
    assert state.attempt_release_calls == 1


@pytest.mark.asyncio
async def test_stale_primary_cooldown_is_revalidated_before_provider_attempt():
    class CountingState(RedisStateBackend):
        def __init__(self) -> None:
            super().__init__(redis=None)
            self.acquisitions: list[str] = []
            self.releases: list[str] = []

        async def acquire_attempt(  # noqa: ANN001, ANN201
            self, deployment_id, capacity, *, lease_ttl_seconds=630
        ):
            self.acquisitions.append(deployment_id)
            return await super().acquire_attempt(
                deployment_id,
                capacity,
                lease_ttl_seconds=lease_ttl_seconds,
            )

        async def release_attempt(self, permit):  # noqa: ANN001, ANN201
            self.releases.append(permit.deployment_id)
            return await super().release_attempt(permit)

    state = CountingState()
    primary = _deployment("dep-primary", priority=0)
    fallback = _deployment("dep-fallback", priority=1)
    planner = _planner(
        state,
        {"group-a": [primary, fallback]},
        strategy=RoutingStrategy.PRIORITY_BASED,
    )
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=planner,
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    routing_context = {ROUTING_MODE_CONTEXT_KEY: "chat", "metadata": {}}
    selected = await planner.select_deployment("group-a", routing_context)
    assert selected is primary
    await state.set_cooldown(primary.deployment_id, 30, "changed-after-selection")
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        return "ok"

    result, served = await manager.execute_with_failover(
        primary,
        "group-a",
        run,
        return_deployment=True,
        routing_context=routing_context,
    )

    assert result == "ok"
    assert served is fallback
    assert attempts == ["dep-fallback"]
    assert state.acquisitions == ["dep-primary", "dep-fallback"]
    assert state.releases == ["dep-fallback"]


@pytest.mark.parametrize("changed_state", ["cooldown", "unhealthy", "capacity"])
@pytest.mark.asyncio
async def test_stale_fallback_dynamic_eligibility_is_revalidated_before_attempt(
    changed_state: str,
):
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-primary", priority=0)
    fallback = _deployment("dep-fallback", priority=1, rpm_limit=1)
    planner = _planner(
        state,
        {"group-a": [primary, fallback]},
        config=RouterConfig(enable_pre_call_checks=True),
        strategy=RoutingStrategy.PRIORITY_BASED,
    )
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=planner,
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    routing_context = {ROUTING_MODE_CONTEXT_KEY: "chat", "metadata": {}}
    selected = await planner.select_deployment("group-a", routing_context)
    assert selected is primary
    if changed_state == "cooldown":
        await state.set_cooldown(fallback.deployment_id, 30, "changed-after-selection")
    elif changed_state == "unhealthy":
        await state.set_health(fallback.deployment_id, False)
    else:
        await state.increment_usage(fallback.deployment_id, 0)

    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        raise TimeoutError(message="upstream timed out")

    with pytest.raises(TimeoutError, match="upstream timed out"):
        await manager.execute_with_failover(
            primary,
            "group-a",
            run,
            routing_context=routing_context,
        )

    assert attempts == ["dep-primary"]
    assert await state.get_active_requests(primary.deployment_id) == 0
    assert await state.get_active_requests(fallback.deployment_id) == 0


@pytest.mark.asyncio
async def test_retry_advances_after_failure_enters_cooldown():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-primary", priority=0)
    fallback = _deployment("dep-fallback", priority=1)
    planner = _planner(
        state,
        {"group-a": [primary, fallback]},
        strategy=RoutingStrategy.PRIORITY_BASED,
    )
    manager = FailoverManager(
        config=FallbackConfig(num_retries=1, retry_after=0, timeout=1.0),
        candidate_planner=planner,
        state_backend=state,
        cooldown_manager=CooldownManager(state, allowed_fails=0),
    )
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        if deployment is primary:
            raise TimeoutError(message="primary timed out")
        return "ok"

    result, served = await manager.execute_with_failover(
        primary,
        "group-a",
        run,
        return_deployment=True,
        routing_context={ROUTING_MODE_CONTEXT_KEY: "chat", "metadata": {}},
    )

    assert result == "ok"
    assert served is fallback
    assert attempts == ["dep-primary", "dep-fallback"]
    assert await state.is_cooled_down(primary.deployment_id)


@pytest.mark.asyncio
async def test_classified_fallback_excludes_deployments_already_visited_by_request():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-primary", priority=0)
    fallback = _deployment("dep-fallback", priority=1)
    planner = _planner(
        state,
        {
            "group-a": [primary],
            "context-group": [primary, fallback],
        },
        strategy=RoutingStrategy.PRIORITY_BASED,
    )
    manager = FailoverManager(
        config=FallbackConfig(
            num_retries=0,
            timeout=1.0,
            context_window_fallbacks={"group-a": ["context-group"]},
        ),
        candidate_planner=planner,
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        if deployment is primary:
            raise InvalidRequestError(message="maximum context length reached")
        return "ok"

    result, served = await manager.execute_with_failover(
        primary,
        "group-a",
        run,
        return_deployment=True,
        routing_context={ROUTING_MODE_CONTEXT_KEY: "chat", "metadata": {}},
    )

    assert result == "ok"
    assert served is fallback
    assert attempts == ["dep-primary", "dep-fallback"]


@pytest.mark.asyncio
async def test_cancelled_attempt_releases_exactly_one_acquired_permit():
    class CountingState(RedisStateBackend):
        def __init__(self) -> None:
            super().__init__(redis=None)
            self.release_calls = 0

        async def release_attempt(self, permit):  # noqa: ANN001, ANN201
            self.release_calls += 1
            return await super().release_attempt(permit)

    state = CountingState()
    primary = _deployment("dep-primary")
    planner = _planner(state, {"group-a": [primary]})
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=10.0),
        candidate_planner=planner,
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    started = asyncio.Event()
    blocked = asyncio.Event()

    async def run(_deployment: Deployment) -> str:
        started.set()
        await blocked.wait()
        return "unreachable"

    task = asyncio.create_task(
        manager.execute_with_failover(
            primary,
            "group-a",
            run,
            routing_context={ROUTING_MODE_CONTEXT_KEY: "chat", "metadata": {}},
        )
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert state.release_calls == 1
    assert await state.get_active_requests(primary.deployment_id) == 0


@pytest.mark.asyncio
async def test_release_failure_does_not_replace_success_or_retry_provider():
    class ReleaseFailingState(RedisStateBackend):
        async def release_attempt(self, permit):  # noqa: ANN001, ANN201
            await super().release_attempt(permit)
            raise ServiceUnavailableError(message="Router state backend unavailable")

    state = ReleaseFailingState(redis=None, degraded_mode="fail_open")
    primary = _deployment("dep-primary")
    planner = _planner(state, {"group-a": [primary]})
    manager = FailoverManager(
        config=FallbackConfig(num_retries=2, timeout=1.0),
        candidate_planner=planner,
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    attempts = 0

    async def run(_deployment: Deployment) -> str:
        nonlocal attempts
        attempts += 1
        return "ok"

    result = await manager.execute_with_failover(
        primary,
        "group-a",
        run,
        routing_context={ROUTING_MODE_CONTEXT_KEY: "chat", "metadata": {}},
    )

    assert result == "ok"
    assert attempts == 1


@pytest.mark.asyncio
async def test_release_failure_does_not_replace_provider_error():
    class ReleaseFailingState(RedisStateBackend):
        async def release_attempt(self, permit):  # noqa: ANN001, ANN201
            await super().release_attempt(permit)
            raise ServiceUnavailableError(message="Router state backend unavailable")

    state = ReleaseFailingState(redis=None, degraded_mode="fail_open")
    primary = _deployment("dep-primary")
    planner = _planner(state, {"group-a": [primary]})
    manager = FailoverManager(
        config=FallbackConfig(num_retries=2, timeout=1.0),
        candidate_planner=planner,
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    provider_error = InvalidRequestError(message="provider rejected the request")
    attempts = 0

    async def run(_deployment: Deployment) -> str:
        nonlocal attempts
        attempts += 1
        raise provider_error

    with pytest.raises(InvalidRequestError) as exc_info:
        await manager.execute_with_failover(
            primary,
            "group-a",
            run,
            routing_context={ROUTING_MODE_CONTEXT_KEY: "chat", "metadata": {}},
        )

    assert exc_info.value is provider_error
    assert attempts == 1


@pytest.mark.asyncio
async def test_cooldown_manager_default_marks_unhealthy_on_third_failure():
    state = RedisStateBackend(redis=None)
    cooldown = CooldownManager(state)

    first = await cooldown.record_failure("dep-a", "boom-1")
    second = await cooldown.record_failure("dep-a", "boom-2")

    assert first is False
    assert second is False
    assert not await state.is_cooled_down("dep-a")
    health = await state.get_health("dep-a")
    assert health.get("healthy", "true") != "false"
    assert int(health.get("consecutive_failures", 0) or 0) == 2

    third = await cooldown.record_failure("dep-a", "boom-3")

    assert third is True
    assert await state.is_cooled_down("dep-a")
    health = await state.get_health("dep-a")
    assert health.get("healthy") == "false"
    assert int(health.get("consecutive_failures", 0) or 0) == 3
    assert health.get("last_error") == "boom-3"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (InvalidRequestError(message="bad input"), False),
        (ServiceUnavailableError(message="local service unavailable"), False),
        (
            ServiceUnavailableError(message="provider unavailable", affects_deployment_health=True),
            True,
        ),
        (TimeoutError(message="timed out"), True),
        (
            httpx.HTTPStatusError(
                "bad request",
                request=httpx.Request("POST", "https://example.com/v1/chat/completions"),
                response=httpx.Response(400),
            ),
            False,
        ),
        (
            httpx.HTTPStatusError(
                "rate limited",
                request=httpx.Request("POST", "https://example.com/v1/chat/completions"),
                response=httpx.Response(429),
            ),
            True,
        ),
        (
            httpx.HTTPStatusError(
                "upstream unavailable",
                request=httpx.Request("POST", "https://example.com/v1/chat/completions"),
                response=httpx.Response(503),
            ),
            True,
        ),
        (httpx.ReadError("connection reset"), True),
        (httpx.PoolTimeout("connection pool exhausted"), False),
    ],
)
def test_affects_deployment_health_matrix(exc: Exception, expected: bool):
    assert affects_deployment_health(exc) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("17", 17),
        ("1.2", 2),
        ("", None),
        ("   ", None),
        ("not-a-date", None),
        ("1e309", None),
    ],
)
def test_parse_retry_after_header_matrix(value: str, expected: int | None):
    assert parse_retry_after_header(value) == expected


def test_parse_retry_after_header_supports_http_dates():
    retry_after = parse_retry_after_header(
        format_datetime(datetime.now(tz=UTC) + timedelta(seconds=2), usegmt=True)
    )

    assert retry_after is not None
    assert retry_after >= 0


@pytest.mark.asyncio
async def test_failover_does_not_cool_down_on_invalid_request_error():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )

    async def run(_deployment: Deployment) -> str:
        raise InvalidRequestError(message="bad input")

    with pytest.raises(InvalidRequestError):
        await manager.execute_with_failover(
            primary_deployment=primary,
            model_group="group-a",
            execute=run,
        )

    assert not await state.is_cooled_down(primary.deployment_id)
    health = await state.get_health(primary.deployment_id)
    assert health.get("healthy", "true") != "false"
    assert int(health.get("consecutive_failures", 0) or 0) == 0
    assert health.get("last_error") is None


@pytest.mark.asyncio
async def test_failover_invalid_request_stops_after_first_deployment():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    fallback = _deployment("dep-b")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary, fallback]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        raise InvalidRequestError(message=f"bad input from {deployment.deployment_id}")

    with pytest.raises(InvalidRequestError, match="bad input from dep-a"):
        await manager.execute_with_failover(
            primary_deployment=primary,
            model_group="group-a",
            execute=run,
        )

    assert attempts == ["dep-a"]


@pytest.mark.asyncio
async def test_failover_http_429_maps_to_rate_limit_error():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state, allowed_fails=0),
    )

    async def run(_deployment: Deployment) -> str:
        raise httpx.HTTPStatusError(
            "rate limited",
            request=httpx.Request("POST", "https://example.com/v1/embeddings"),
            response=httpx.Response(429, headers={"Retry-After": "7"}),
        )

    with pytest.raises(RateLimitError) as exc_info:
        await manager.execute_with_failover(
            primary_deployment=primary,
            model_group="group-a",
            execute=run,
        )

    assert exc_info.value.retry_after == 7
    assert await state.is_cooled_down(primary.deployment_id)


@pytest.mark.asyncio
async def test_failover_http_503_maps_to_health_affecting_service_unavailable_error():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state, allowed_fails=0),
    )

    async def run(_deployment: Deployment) -> str:
        raise httpx.HTTPStatusError(
            "upstream unavailable",
            request=httpx.Request("POST", "https://example.com/v1/embeddings"),
            response=httpx.Response(503),
        )

    with pytest.raises(ServiceUnavailableError) as exc_info:
        await manager.execute_with_failover(
            primary_deployment=primary,
            model_group="group-a",
            execute=run,
        )

    assert exc_info.value.affects_deployment_health is True
    assert await state.is_cooled_down(primary.deployment_id)


@pytest.mark.asyncio
async def test_failover_transport_error_maps_to_health_affecting_service_unavailable_error():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state, allowed_fails=0),
    )

    async def run(_deployment: Deployment) -> str:
        raise httpx.ReadError("connection reset")

    with pytest.raises(ServiceUnavailableError) as exc_info:
        await manager.execute_with_failover(
            primary_deployment=primary,
            model_group="group-a",
            execute=run,
        )

    assert exc_info.value.affects_deployment_health is True
    assert await state.is_cooled_down(primary.deployment_id)


@pytest.mark.asyncio
async def test_failover_returns_structured_no_healthy_deployments_error_when_all_candidates_cooled_down():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    await state.set_cooldown(primary.deployment_id, 30, "manual")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state, allowed_fails=0),
    )

    async def run(_deployment: Deployment) -> str:
        return "unreachable"

    with pytest.raises(ServiceUnavailableError) as exc_info:
        await manager.execute_with_failover(
            primary_deployment=primary,
            model_group="group-a",
            execute=run,
        )

    assert exc_info.value.code == NO_HEALTHY_DEPLOYMENTS_CODE
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_failover_http_timeout_maps_to_timeout_error():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state, allowed_fails=0),
    )

    async def run(_deployment: Deployment) -> str:
        raise httpx.ReadTimeout("upstream timed out")

    with pytest.raises(TimeoutError, match="upstream timed out") as exc_info:
        await manager.execute_with_failover(
            primary_deployment=primary,
            model_group="group-a",
            execute=run,
        )

    assert exc_info.value.affects_deployment_health is True
    assert await state.is_cooled_down(primary.deployment_id)


@pytest.mark.asyncio
async def test_failover_local_execution_error_does_not_affect_deployment_health_or_fallback():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    fallback = _deployment("dep-b")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary, fallback]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        raise RuntimeError("local bug")

    with pytest.raises(ServiceUnavailableError) as exc_info:
        await manager.execute_with_failover(
            primary_deployment=primary,
            model_group="group-a",
            execute=run,
        )

    assert str(exc_info.value) == "local bug"
    assert affects_deployment_health(exc_info.value) is False
    assert attempts == ["dep-a"]
    assert not await state.is_cooled_down(primary.deployment_id)
    health = await state.get_health(primary.deployment_id)
    assert health.get("healthy", "true") != "false"
    assert int(health.get("consecutive_failures", 0) or 0) == 0
    assert health.get("last_error") is None


@pytest.mark.asyncio
async def test_failover_classified_fallback_local_error_does_not_cool_down_or_try_next_fallback():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    fallback_a = _deployment("dep-b")
    fallback_b = _deployment("dep-c")
    manager = FailoverManager(
        config=FallbackConfig(
            num_retries=0,
            timeout=1.0,
            context_window_fallbacks={"group-a": ["ctx-fallbacks"]},
        ),
        candidate_planner=_planner(
            state,
            {"group-a": [primary], "ctx-fallbacks": [fallback_a, fallback_b]},
        ),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        if deployment.deployment_id == "dep-a":
            raise InvalidRequestError(message="maximum context length reached")
        raise RuntimeError("local fallback bug")

    with pytest.raises(ServiceUnavailableError, match="local fallback bug"):
        await manager.execute_with_failover(
            primary_deployment=primary,
            model_group="group-a",
            execute=run,
        )

    assert attempts == ["dep-a", "dep-b"]
    assert not await state.is_cooled_down(primary.deployment_id)
    assert not await state.is_cooled_down(fallback_a.deployment_id)
    assert not await state.is_cooled_down(fallback_b.deployment_id)


@pytest.mark.asyncio
async def test_failover_classified_fallback_continues_after_upstream_service_unavailable():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    fallback_a = _deployment("dep-b")
    fallback_b = _deployment("dep-c")
    manager = FailoverManager(
        config=FallbackConfig(
            num_retries=0,
            timeout=1.0,
            context_window_fallbacks={"group-a": ["ctx-fallbacks"]},
        ),
        candidate_planner=_planner(
            state,
            {"group-a": [primary], "ctx-fallbacks": [fallback_a, fallback_b]},
        ),
        state_backend=state,
        cooldown_manager=CooldownManager(state, allowed_fails=0),
    )
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        if deployment.deployment_id == "dep-a":
            raise InvalidRequestError(message="maximum context length reached")
        if deployment.deployment_id == "dep-b":
            raise httpx.HTTPStatusError(
                "upstream unavailable",
                request=httpx.Request("POST", "https://example.com/v1/chat/completions"),
                response=httpx.Response(503),
            )
        return "ok"

    data, served = await manager.execute_with_failover(
        primary_deployment=primary,
        model_group="group-a",
        execute=run,
        return_deployment=True,
    )

    assert data == "ok"
    assert served.deployment_id == "dep-c"
    assert attempts == ["dep-a", "dep-b", "dep-c"]
    assert not await state.is_cooled_down(primary.deployment_id)
    assert await state.is_cooled_down(fallback_a.deployment_id)
    assert not await state.is_cooled_down(fallback_b.deployment_id)


@pytest.mark.asyncio
async def test_failover_applies_timeout_resolver_to_classified_fallbacks():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    fallback_a = _deployment("dep-b")
    fallback_b = _deployment("dep-c")
    manager = FailoverManager(
        config=FallbackConfig(
            num_retries=0,
            timeout=1.0,
            context_window_fallbacks={"group-a": ["ctx-fallbacks"]},
        ),
        candidate_planner=_planner(
            state,
            {"group-a": [primary], "ctx-fallbacks": [fallback_a, fallback_b]},
        ),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        if deployment.deployment_id == "dep-a":
            raise InvalidRequestError(message="maximum context length reached")
        if deployment.deployment_id == "dep-b":
            await asyncio.sleep(0.05)
            return "late-fallback"
        return "fallback-ok"

    def timeout_for_deployment(deployment: Deployment) -> float:
        return 0.01 if deployment.deployment_id == "dep-b" else 1.0

    data, served = await manager.execute_with_failover(
        primary_deployment=primary,
        model_group="group-a",
        execute=run,
        return_deployment=True,
        timeout_for_deployment=timeout_for_deployment,
    )

    assert data == "fallback-ok"
    assert served.deployment_id == "dep-c"
    assert attempts == ["dep-a", "dep-b", "dep-c"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "headers"),
    [
        (429, {"Retry-After": "0"}),
        (503, {}),
    ],
)
async def test_failover_retries_transient_raw_http_errors(
    status_code: int, headers: dict[str, str]
):
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    attempts = {"count": 0}

    async def run(_deployment: Deployment) -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.HTTPStatusError(
                f"upstream failed with status {status_code}",
                request=httpx.Request("POST", "https://example.com/v1/embeddings"),
                response=httpx.Response(status_code, headers=headers),
            )
        return "ok"

    data, served = await manager.execute_with_failover(
        primary_deployment=primary,
        model_group="group-a",
        execute=run,
        return_deployment=True,
        retry_max_attempts=1,
    )

    assert data == "ok"
    assert served.deployment_id == "dep-a"
    assert attempts["count"] == 2
    health = await state.get_health(primary.deployment_id)
    assert health.get("healthy", "true") != "false"
    assert int(health.get("consecutive_failures", 0) or 0) == 0


@pytest.mark.asyncio
async def test_failover_retries_raw_http_timeout_when_route_policy_targets_timeout():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    attempts = {"count": 0}

    async def run(_deployment: Deployment) -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ReadTimeout("upstream timed out")
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
    health = await state.get_health(primary.deployment_id)
    assert health.get("healthy", "true") != "false"
    assert int(health.get("consecutive_failures", 0) or 0) == 0


@pytest.mark.asyncio
async def test_retry_budget_is_shared_across_fallback_candidates():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    fallback = _deployment("dep-b")
    manager = FailoverManager(
        config=FallbackConfig(
            num_retries=1,
            retry_after=0.001,
            timeout=1.0,
            backoff_jitter=False,
            fallbacks={"group-a": ["group-b"]},
        ),
        candidate_planner=_planner(
            state,
            {"group-a": [primary], "group-b": [fallback]},
        ),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        raise TimeoutError(message="provider timed out")

    with pytest.raises(TimeoutError):
        await manager.execute_with_failover(primary, "group-a", run)

    assert attempts == ["dep-a", "dep-a", "dep-b"]


@pytest.mark.asyncio
async def test_total_deadline_bounds_retry_backoff():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(
            num_retries=1,
            retry_after=0.05,
            timeout=0.01,
            backoff_jitter=False,
        ),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    attempts = 0

    async def run(_deployment: Deployment) -> str:
        nonlocal attempts
        attempts += 1
        raise TimeoutError(message="provider timed out")

    with pytest.raises(TimeoutError, match="Request deadline exceeded"):
        await manager.execute_with_failover(primary, "group-a", run)

    assert attempts == 1


@pytest.mark.asyncio
async def test_managed_attempt_holds_capacity_until_caller_releases():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )

    async def run(_deployment: Deployment) -> str:
        return "stream-open"

    managed = await manager.execute_managed_with_failover(
        primary,
        "group-a",
        run,
        routing_context={ROUTING_MODE_CONTEXT_KEY: "chat", "metadata": {}},
    )

    assert managed.value == "stream-open"
    assert managed.deployment is primary
    assert await state.get_active_requests(primary.deployment_id) == 1

    await managed.release()
    await managed.release()

    assert await state.get_active_requests(primary.deployment_id) == 0


@pytest.mark.asyncio
async def test_passive_health_tracker_ignores_invalid_request_errors():
    state = RedisStateBackend(redis=None)
    tracker = PassiveHealthTracker(state_backend=state, failure_threshold=1)

    await tracker.record_request_outcome(
        "dep-a",
        success=False,
        error="bad request",
        exc=InvalidRequestError(message="bad request"),
    )

    health = await state.get_health("dep-a")
    assert health.get("healthy", "true") != "false"
    assert int(health.get("consecutive_failures", 0) or 0) == 0
    assert health.get("last_error") is None


@pytest.mark.asyncio
async def test_failover_treats_pool_timeout_as_gateway_capacity_error():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )

    async def run(deployment: Deployment):  # noqa: ARG001, ANN202
        raise httpx.PoolTimeout("connection pool exhausted")

    with pytest.raises(GatewayCapacityError) as exc_info:
        await manager.execute_with_failover(primary, "group-a", run)

    assert exc_info.value.code == "upstream_pool_timeout"
    assert exc_info.value.affects_deployment_health is False
    health = await state.get_health(primary.deployment_id)
    assert health.get("healthy", "true") != "false"
    assert int(health.get("consecutive_failures", 0) or 0) == 0


@pytest.mark.asyncio
async def test_provider_health_check_preserves_pool_timeout_and_marks_pool_timeout_local():
    captured: dict[str, object] = {}

    class FakeHTTPClient:
        async def get(self, url, headers=None, timeout=None):  # noqa: ANN001, ANN201
            del url, headers
            captured["timeout"] = timeout
            raise httpx.PoolTimeout("connection pool exhausted")

    general_settings = type("GeneralSettings", (), {"upstream_http_pool_timeout_seconds": 2})()

    result = await probe_provider_health(
        FakeHTTPClient(),  # type: ignore[arg-type]
        {"provider": "openai", "model": "openai/gpt-4o-mini", "api_key": "provider-key"},
        default_openai_base_url="https://api.openai.com/v1",
        general_settings=general_settings,
    )

    timeout = captured["timeout"]
    assert getattr(timeout, "read") == 10.0
    assert getattr(timeout, "pool") == 2.0
    assert result.healthy is False
    assert result.affects_deployment_health is False
    assert result.error == "Gateway upstream connection pool exhausted"


@pytest.mark.asyncio
async def test_provider_health_check_caps_pool_timeout_below_health_wrapper_timeout():
    captured: dict[str, object] = {}

    class FakeHTTPClient:
        async def get(self, url, headers=None, timeout=None):  # noqa: ANN001, ANN201
            del url, headers
            captured["timeout"] = timeout
            raise httpx.PoolTimeout("connection pool exhausted")

    general_settings = type("GeneralSettings", (), {"upstream_http_pool_timeout_seconds": 30})()

    result = await probe_provider_health(
        FakeHTTPClient(),  # type: ignore[arg-type]
        {"provider": "openai", "model": "openai/gpt-4o-mini", "api_key": "provider-key"},
        default_openai_base_url="https://api.openai.com/v1",
        general_settings=general_settings,
        health_check_timeout_seconds=5,
    )

    timeout = captured["timeout"]
    assert getattr(timeout, "pool") == 4.0
    assert result.healthy is False
    assert result.affects_deployment_health is False


@pytest.mark.asyncio
async def test_provider_health_check_defaults_unresolved_provider_to_openai():
    captured: dict[str, object] = {}

    class FakeHTTPClient:
        async def get(self, url, headers=None, timeout=None):  # noqa: ANN001, ANN201
            captured["url"] = url
            captured["headers"] = dict(headers or {})
            captured["timeout"] = timeout
            return httpx.Response(200, json={"data": []}, request=httpx.Request("GET", url))

    result = await probe_provider_health(
        FakeHTTPClient(),  # type: ignore[arg-type]
        {"model": "gpt-4o-mini", "api_key": "provider-key"},
        default_openai_base_url="https://api.openai.com/v1",
    )

    assert result.healthy is True
    assert captured["url"] == "https://api.openai.com/v1/models"
    assert captured["headers"] == {"Authorization": "Bearer provider-key"}
    timeout = captured["timeout"]
    assert getattr(timeout, "read") == 10.0


@pytest.mark.asyncio
async def test_provider_health_check_supports_elevenlabs_with_default_base_url():
    captured: dict[str, object] = {}

    class FakeHTTPClient:
        async def get(self, url, headers=None, timeout=None):  # noqa: ANN001, ANN201
            captured["url"] = url
            captured["headers"] = dict(headers or {})
            captured["timeout"] = timeout
            return httpx.Response(200, json={"models": []}, request=httpx.Request("GET", url))

    result = await probe_provider_health(
        FakeHTTPClient(),  # type: ignore[arg-type]
        {
            "provider": "elevenlabs",
            "model": "elevenlabs/eleven_multilingual_v2",
            "api_key": "provider-key",
        },
        default_openai_base_url="https://api.openai.com/v1",
    )

    assert result.healthy is True
    assert result.status_code == 200
    assert captured["url"] == "https://api.elevenlabs.io/v1/models"
    assert captured["headers"] == {"xi-api-key": "provider-key"}
    timeout = captured["timeout"]
    assert getattr(timeout, "read") == 10.0


@pytest.mark.asyncio
async def test_provider_health_check_supports_elevenlabs_custom_base_url():
    captured: dict[str, object] = {}

    class FakeHTTPClient:
        async def get(self, url, headers=None, timeout=None):  # noqa: ANN001, ANN201
            captured["url"] = url
            captured["headers"] = dict(headers or {})
            del timeout
            return httpx.Response(200, json={"models": []}, request=httpx.Request("GET", url))

    result = await probe_provider_health(
        FakeHTTPClient(),  # type: ignore[arg-type]
        {
            "provider": "elevenlabs",
            "model": "elevenlabs/eleven_multilingual_v2",
            "api_key": "provider-key",
            "api_base": "https://elevenlabs-proxy.example/v1/",
        },
        default_openai_base_url="https://api.openai.com/v1",
    )

    assert result.healthy is True
    assert captured["url"] == "https://elevenlabs-proxy.example/v1/models"
    assert captured["headers"] == {"xi-api-key": "provider-key"}


@pytest.mark.asyncio
async def test_provider_health_check_marks_elevenlabs_missing_api_key_unhealthy():
    class FakeHTTPClient:
        async def get(self, url, headers=None, timeout=None):  # noqa: ANN001, ANN201
            raise AssertionError("health check must not call upstream without an API key")

    result = await probe_provider_health(
        FakeHTTPClient(),  # type: ignore[arg-type]
        {"provider": "elevenlabs", "model": "elevenlabs/eleven_multilingual_v2"},
        default_openai_base_url="https://api.openai.com/v1",
    )

    assert result.healthy is False
    assert result.error == "Provider API key is missing"


@pytest.mark.asyncio
async def test_background_health_checker_ignores_non_health_affecting_result():
    state = RedisStateBackend(redis=None)
    deployment = _deployment("dep-a")
    checker = BackgroundHealthChecker(
        config=HealthCheckConfig(enabled=False),
        deployment_registry={"group-a": [deployment]},
        state_backend=state,
        checker=lambda _: asyncio.sleep(
            0,
            result=HealthProbeResult(
                healthy=False,
                error="Gateway upstream connection pool exhausted",
                affects_deployment_health=False,
            ),
        ),
    )

    result = await checker.check_deployment_once(deployment)

    assert result.affects_deployment_health is False
    health = await state.get_health(deployment.deployment_id)
    assert health.get("healthy", "true") != "false"
    assert int(health.get("consecutive_failures", 0) or 0) == 0
    assert health.get("last_error") is None


def test_failover_event_history_is_bounded_and_preserves_recent_order():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(event_history_size=3),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )

    manager._record_fallback_event("group-a", "dep-1", "dep-2", "retry", "timeout", "one", 1, False)
    manager._record_fallback_event("group-a", "dep-2", "dep-3", "retry", "timeout", "two", 2, False)
    manager._record_fallback_event(
        "group-a", "dep-3", "dep-4", "retry", "timeout", "three", 3, False
    )
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
        candidate_planner=_planner(state, {"group-a": [primary]}),
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
