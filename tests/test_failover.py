from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from src.batch.retry import BatchResponseShapeError
from src.chat.stream_response import DeadlineStreamingResponse
from src.models.errors import (
    FailureClassification,
    GatewayCapacityError,
    InvalidRequestError,
    NO_HEALTHY_DEPLOYMENTS_CODE,
    RateLimitError,
    ServiceUnavailableError,
    TimeoutError,
    parse_retry_after_header,
)
from src.providers.anthropic import AnthropicAdapter
from src.providers.azure import AzureOpenAIAdapter
from src.providers.bedrock import BedrockAdapter
from src.providers.gemini import GeminiAdapter
from src.providers.healthcheck import HealthProbeResult, probe_provider_health
from src.providers.openai import OpenAIAdapter
from src.providers.base import invalid_provider_response_error
from src.router import (
    AttemptCapacity,
    BackgroundHealthChecker,
    CooldownManager,
    ErrorClassification,
    FallbackConfig,
    FailoverManager,
    HealthCheckInProgressError,
    HealthCheckConfig,
    HealthEndpointHandler,
    RedisStateBackend,
    ProviderAttemptResult,
    RequestDeadline,
    ROUTING_MODE_CONTEXT_KEY,
    RouteGroupPolicy,
    Router,
    RouterConfig,
    RoutingStrategy,
    get_failover_attempt_context,
    get_failover_original_error,
)
from src.router.health_policy import affects_deployment_health
from src.router.router import Deployment
from tests.conftest import FakeRedis


def test_fallback_config_freezes_all_fallback_maps() -> None:
    general = {"group-a": ["general"]}
    context = {"group-a": ["context"]}
    content = {"group-a": ["content"]}

    config = FallbackConfig(
        fallbacks=general,
        context_window_fallbacks=context,
        content_policy_fallbacks=content,
    )
    general["group-a"].append("mutated")
    context["group-a"].append("mutated")
    content["group-a"].append("mutated")

    assert config.fallbacks["group-a"] == ("general",)
    assert config.context_window_fallbacks["group-a"] == ("context",)
    assert config.content_policy_fallbacks["group-a"] == ("content",)
    with pytest.raises(TypeError):
        config.fallbacks["group-a"] = ("replacement",)  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        config.num_retries = 1  # type: ignore[misc]


def test_error_classification_compatibility_facade_uses_typed_metadata_only() -> None:
    typed = InvalidRequestError(
        message="Provider rejected request",
        failure_classification=FailureClassification.CONTEXT_WINDOW,
    )
    untyped = InvalidRequestError(message="maximum context length reached")

    assert ErrorClassification.classify(typed) is FailureClassification.CONTEXT_WINDOW
    assert ErrorClassification.classify(untyped) is FailureClassification.GENERIC


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
async def test_failover_does_not_retry_wrapped_nonretryable_provider_error(
    monkeypatch: pytest.MonkeyPatch,
):
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=3, retry_after=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state, allowed_fails=10),
    )
    monkeypatch.setattr(manager, "_compute_backoff", lambda *_: 0.0)
    attempts = 0

    async def run(_deployment: Deployment) -> str:
        nonlocal attempts
        attempts += 1
        raise BatchResponseShapeError("malformed provider result")

    with pytest.raises(ServiceUnavailableError) as exc_info:
        await manager.execute_with_failover(primary, "group-a", run)

    assert attempts == 1
    assert isinstance(get_failover_original_error(exc_info.value), BatchResponseShapeError)


@pytest.mark.asyncio
async def test_failover_records_mixed_result_health_once_without_replaying_attempt(
    monkeypatch: pytest.MonkeyPatch,
):
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=3, retry_after=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state, allowed_fails=10),
    )
    monkeypatch.setattr(manager, "_compute_backoff", lambda *_: 0.0)
    attempts = 0

    async def run(_deployment: Deployment) -> ProviderAttemptResult[str]:
        nonlocal attempts
        attempts += 1
        return ProviderAttemptResult(
            value="mixed-result",
            health_error=ServiceUnavailableError(
                message="one item failed upstream",
                affects_deployment_health=True,
            ),
        )

    result = await manager.execute_with_failover(primary, "group-a", run)

    assert result == "mixed-result"
    assert attempts == 1
    health = await state.get_health(primary.deployment_id)
    assert health["consecutive_failures"] == "1"


@pytest.mark.asyncio
async def test_failover_does_not_replay_provider_success_when_state_reporting_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    cooldown = CooldownManager(state)
    manager = FailoverManager(
        config=FallbackConfig(num_retries=3, retry_after=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=cooldown,
    )
    attempts = 0

    async def fail_state_update(*_args, **_kwargs):
        raise ServiceUnavailableError(message="router state unavailable")

    async def run(_deployment: Deployment) -> str:
        nonlocal attempts
        attempts += 1
        return "provider-result"

    monkeypatch.setattr(state, "record_latency", fail_state_update)
    monkeypatch.setattr(cooldown, "record_success", fail_state_update)

    result = await manager.execute_with_failover(primary, "group-a", run)

    assert result == "provider-result"
    assert attempts == 1


@pytest.mark.asyncio
async def test_failover_attaches_empty_attempt_context_to_planning_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    planner = _planner(state, {"group-a": [primary]})
    manager = FailoverManager(
        config=FallbackConfig(timeout=1.0),
        candidate_planner=planner,
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )

    async def fail_planning(*_args, **_kwargs):
        raise RuntimeError("planning unavailable")

    monkeypatch.setattr(planner, "plan_deployments", fail_planning)
    with pytest.raises(RuntimeError) as exc_info:
        await manager.execute_with_failover(primary, "group-a", lambda _deployment: None)

    context = get_failover_attempt_context(exc_info.value)
    assert context is not None
    assert context.model_group == "group-a"
    assert context.attempted_deployment_ids == ()


@pytest.mark.asyncio
async def test_failover_applies_retry_override(monkeypatch: pytest.MonkeyPatch):
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    monkeypatch.setattr(manager, "_compute_backoff", lambda *_: 0.0)
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
    state._health[unhealthy.health_ref] = {"healthy": "false"}
    await CooldownManager(state).manual_cooldown(cooled.deployment_id, 30, "manual")
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
async def test_general_fallback_cannot_cross_route_group_workload_mode():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-primary", mode="chat")
    embedding_fallback = _deployment("dep-embedding", mode="embedding")
    planner = _planner(
        state,
        {
            "group-a": [primary],
            "embedding-fallback": [embedding_fallback],
        },
        config=RouterConfig(
            route_group_policies={"embedding-fallback": RouteGroupPolicy(workload_mode="embedding")}
        ),
    )
    manager = FailoverManager(
        config=FallbackConfig(
            num_retries=0,
            timeout=1,
            fallbacks={"group-a": ["embedding-fallback"]},
        ),
        candidate_planner=planner,
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        raise TimeoutError(message="provider timed out")

    with pytest.raises(TimeoutError):
        await manager.execute_with_failover(
            primary,
            "group-a",
            run,
            routing_context={ROUTING_MODE_CONTEXT_KEY: "chat"},
        )

    assert attempts == ["dep-primary"]


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
            raise InvalidRequestError(
                message="Provider rejected request",
                failure_classification=FailureClassification.CONTEXT_WINDOW,
            )
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
async def test_content_policy_classification_selects_content_fallback_map() -> None:
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-primary")
    fallback = _deployment("dep-content-fallback")
    manager = FailoverManager(
        config=FallbackConfig(
            content_policy_fallbacks={"group-a": ["content-group"]},
        ),
        candidate_planner=_planner(
            state,
            {"group-a": [primary], "content-group": [fallback]},
        ),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        if deployment is primary:
            raise InvalidRequestError(
                message="Provider rejected request",
                failure_classification=FailureClassification.CONTENT_POLICY,
            )
        return "content-fallback-ok"

    result, served = await manager.execute_with_failover(
        primary,
        "group-a",
        run,
        return_deployment=True,
        routing_context={ROUTING_MODE_CONTEXT_KEY: "chat", "metadata": {}},
    )

    assert result == "content-fallback-ok"
    assert served is fallback
    assert attempts == ["dep-primary", "dep-content-fallback"]


@pytest.mark.asyncio
async def test_health_affecting_classification_uses_specialized_then_general_fallback() -> None:
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-primary")
    context_fallback = _deployment("dep-context-fallback")
    general_fallback = _deployment("dep-general-fallback")
    manager = FailoverManager(
        config=FallbackConfig(
            fallbacks={"group-a": ["general-group"]},
            context_window_fallbacks={"group-a": ["context-group"]},
        ),
        candidate_planner=_planner(
            state,
            {
                "group-a": [primary],
                "context-group": [context_fallback],
                "general-group": [general_fallback],
            },
        ),
        state_backend=state,
        cooldown_manager=CooldownManager(state, allowed_fails=0),
    )
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        if deployment is primary:
            raise ServiceUnavailableError(
                message="Provider unavailable",
                affects_deployment_health=True,
                failure_classification=FailureClassification.CONTEXT_WINDOW,
            )
        if deployment is context_fallback:
            raise ServiceUnavailableError(
                message="Provider unavailable",
                affects_deployment_health=True,
                failure_classification=FailureClassification.GENERIC,
            )
        return "general-fallback-ok"

    result, served = await manager.execute_with_failover(
        primary,
        "group-a",
        run,
        return_deployment=True,
        routing_context={ROUTING_MODE_CONTEXT_KEY: "chat", "metadata": {}},
    )

    assert result == "general-fallback-ok"
    assert served is general_fallback
    assert attempts == ["dep-primary", "dep-context-fallback", "dep-general-fallback"]
    assert await state.is_cooled_down(primary.deployment_id)
    assert await state.is_cooled_down(context_fallback.deployment_id)
    assert not await state.is_cooled_down(general_fallback.deployment_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_type", "provider_response", "expected_fallback"),
    [
        (
            GeminiAdapter,
            {"promptFeedback": {"blockReason": "SAFETY"}},
            "dep-content-fallback",
        ),
        (
            AnthropicAdapter,
            {
                "content": [],
                "stop_reason": "refusal",
                "usage": {"input_tokens": 4, "output_tokens": 0},
            },
            "dep-content-fallback",
        ),
        (
            AnthropicAdapter,
            {
                "content": [],
                "stop_reason": "model_context_window_exceeded",
                "usage": {"input_tokens": 4, "output_tokens": 0},
            },
            "dep-context-fallback",
        ),
        (
            BedrockAdapter,
            {
                "output": {"message": {"content": []}},
                "stopReason": "model_context_window_exceeded",
                "usage": {"inputTokens": 4, "outputTokens": 0, "totalTokens": 4},
            },
            "dep-context-fallback",
        ),
        (
            AzureOpenAIAdapter,
            {
                "id": "chatcmpl-filtered",
                "object": "chat.completion",
                "created": 1700000000,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": None},
                        "finish_reason": "content_filter",
                    }
                ],
            },
            "dep-content-fallback",
        ),
        (
            OpenAIAdapter,
            {
                "id": "chatcmpl-filtered",
                "object": "chat.completion",
                "created": 1700000000,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": None},
                        "finish_reason": "content_filter",
                    }
                ],
            },
            "dep-content-fallback",
        ),
    ],
)
async def test_documented_provider_response_failure_selects_specialized_fallback(
    adapter_type,
    provider_response: dict,
    expected_fallback: str,
) -> None:
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-primary")
    content_fallback = _deployment("dep-content-fallback")
    context_fallback = _deployment("dep-context-fallback")
    manager = FailoverManager(
        config=FallbackConfig(
            content_policy_fallbacks={"group-a": ["content-group"]},
            context_window_fallbacks={"group-a": ["context-group"]},
        ),
        candidate_planner=_planner(
            state,
            {
                "group-a": [primary],
                "content-group": [content_fallback],
                "context-group": [context_fallback],
            },
        ),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    adapter = adapter_type(httpx.AsyncClient())
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        if deployment is primary:
            await adapter.translate_response(provider_response, model_name="provider-model")
            raise AssertionError("provider rejection must not translate as success")
        return "fallback-ok"

    try:
        result, served = await manager.execute_with_failover(
            primary,
            "group-a",
            run,
            return_deployment=True,
            routing_context={ROUTING_MODE_CONTEXT_KEY: "chat", "metadata": {}},
        )
    finally:
        await adapter.http_client.aclose()

    assert result == "fallback-ok"
    assert served.deployment_id == expected_fallback
    assert attempts == ["dep-primary", expected_fallback]


@pytest.mark.asyncio
async def test_anthropic_stream_policy_stop_before_first_chunk_selects_specialized_fallback():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-primary")
    fallback = _deployment("dep-content-fallback")
    manager = FailoverManager(
        config=FallbackConfig(
            content_policy_fallbacks={"group-a": ["content-group"]},
        ),
        candidate_planner=_planner(
            state,
            {"group-a": [primary], "content-group": [fallback]},
        ),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    adapter = AnthropicAdapter(httpx.AsyncClient())
    attempts: list[str] = []

    async def provider_lines():
        yield 'data: {"type":"message_start","message":{"id":"msg_1","model":"claude"}}'
        yield 'data: {"type":"message_delta","delta":{"stop_reason":"refusal"}}'
        yield 'data: {"type":"message_stop"}'

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        if deployment is primary:
            translated = adapter.translate_stream(provider_lines()).__aiter__()
            return await anext(translated)
        return "fallback-first-chunk"

    try:
        result, served = await manager.execute_with_failover(
            primary,
            "group-a",
            run,
            return_deployment=True,
            routing_context={ROUTING_MODE_CONTEXT_KEY: "chat", "metadata": {}},
        )

        assert result == "fallback-first-chunk"
        assert served is fallback
        assert attempts == [primary.deployment_id, fallback.deployment_id]
    finally:
        await adapter.http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_http_error", [False, True])
async def test_untyped_error_text_cannot_trigger_classified_fallback(
    raw_http_error: bool,
) -> None:
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-primary")
    fallback = _deployment("dep-context-fallback")
    manager = FailoverManager(
        config=FallbackConfig(
            context_window_fallbacks={"group-a": ["context-group"]},
        ),
        candidate_planner=_planner(
            state,
            {"group-a": [primary], "context-group": [fallback]},
        ),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        if raw_http_error:
            response = httpx.Response(
                400,
                json={
                    "error": {
                        "code": "context_length_exceeded",
                        "message": "maximum context length sk-upstream",
                    }
                },
                request=httpx.Request("POST", "https://internal.provider.example/v1/chat"),
            )
            raise httpx.HTTPStatusError(
                "maximum context length",
                request=response.request,
                response=response,
            )
        raise InvalidRequestError(message="maximum context length reached")

    with pytest.raises(
        InvalidRequestError,
        match="Provider rejected request" if raw_http_error else "maximum context length",
    ):
        await manager.execute_with_failover(
            primary,
            "group-a",
            run,
            routing_context={ROUTING_MODE_CONTEXT_KEY: "chat", "metadata": {}},
        )

    assert attempts == ["dep-primary"]


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
            self.acquisitions.append(deployment_id.deployment_id)
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
    await CooldownManager(state).manual_cooldown(
        primary.deployment_id,
        30,
        "changed-after-selection",
    )
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
        await CooldownManager(state).manual_cooldown(
            fallback.deployment_id,
            30,
            "changed-after-selection",
        )
    elif changed_state == "unhealthy":
        state._health[fallback.health_ref] = {"healthy": "false"}
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
            raise InvalidRequestError(
                message="Provider rejected request",
                failure_classification=FailureClassification.CONTEXT_WINDOW,
            )
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


@pytest.mark.asyncio
async def test_failover_records_each_failed_deployment_once():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-primary", priority=0)
    fallback = _deployment("dep-fallback", priority=1)
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=_planner(
            state,
            {"group-a": [primary, fallback]},
            strategy=RoutingStrategy.PRIORITY_BASED,
        ),
        state_backend=state,
        cooldown_manager=CooldownManager(state, allowed_fails=10),
    )

    async def run(deployment: Deployment) -> str:
        raise ServiceUnavailableError(
            message=f"{deployment.deployment_id} unavailable",
            affects_deployment_health=True,
        )

    with pytest.raises(ServiceUnavailableError) as exc_info:
        await manager.execute_with_failover(primary, "group-a", run)

    primary_health = await state.get_health(primary.deployment_id)
    fallback_health = await state.get_health(fallback.deployment_id)
    assert primary_health["consecutive_failures"] == "1"
    assert fallback_health["consecutive_failures"] == "1"
    context = get_failover_attempt_context(exc_info.value)
    assert context is not None
    assert context.model_group == "group-a"
    assert context.attempted_deployment_ids == ("dep-primary", "dep-fallback")
    assert context.last_attempted_deployment_id == "dep-fallback"


@pytest.mark.asyncio
async def test_failover_records_explicit_health_affecting_execution_failure_once():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-primary")
    manager = FailoverManager(
        config=FallbackConfig(num_retries=0, timeout=1.0),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state, allowed_fails=10),
    )

    async def run(_deployment: Deployment) -> str:
        exc = RuntimeError("malformed upstream response")
        exc.affects_deployment_health = True  # type: ignore[attr-defined]
        raise exc

    with pytest.raises(ServiceUnavailableError, match="Service unavailable") as exc_info:
        await manager.execute_with_failover(primary, "group-a", run)

    health = await state.get_health(primary.deployment_id)
    assert health["consecutive_failures"] == "1"
    assert health["last_error"] == "Service unavailable"
    original = get_failover_original_error(exc_info.value)
    assert isinstance(original, RuntimeError)
    assert str(original) == "malformed upstream response"


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
    await CooldownManager(state).manual_cooldown(primary.deployment_id, 30, "manual")
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

    with pytest.raises(TimeoutError, match="Request timeout") as exc_info:
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

    assert str(exc_info.value) == "Service unavailable"
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
            raise InvalidRequestError(
                message="Provider rejected request",
                failure_classification=FailureClassification.CONTEXT_WINDOW,
            )
        raise RuntimeError("local fallback bug")

    with pytest.raises(ServiceUnavailableError, match="Service unavailable"):
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
async def test_malformed_provider_success_uses_general_fallback_chain():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    fallback = _deployment("dep-b")
    manager = FailoverManager(
        config=FallbackConfig(
            num_retries=0,
            timeout=1.0,
            fallbacks={"group-a": ["general-fallbacks"]},
        ),
        candidate_planner=_planner(
            state,
            {"group-a": [primary], "general-fallbacks": [fallback]},
        ),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    attempts: list[str] = []

    async def run(deployment: Deployment) -> str:
        attempts.append(deployment.deployment_id)
        if deployment.deployment_id == primary.deployment_id:
            raise invalid_provider_response_error()
        return "ok"

    result = await manager.execute_with_failover(primary, "group-a", run)

    assert result == "ok"
    assert attempts == [primary.deployment_id, fallback.deployment_id]
    primary_health = await state.get_health(primary.deployment_id)
    assert primary_health["last_error"] == "Provider returned an invalid response"


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
            raise InvalidRequestError(
                message="Provider rejected request",
                failure_classification=FailureClassification.CONTEXT_WINDOW,
            )
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
            raise InvalidRequestError(
                message="Provider rejected request",
                failure_classification=FailureClassification.CONTEXT_WINDOW,
            )
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
async def test_failover_reuses_caller_owned_request_deadline():
    state = RedisStateBackend(redis=None)
    primary = _deployment("dep-a")
    manager = FailoverManager(
        config=FallbackConfig(timeout=10.0),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    deadline = RequestDeadline(expires_at=asyncio.get_running_loop().time())
    attempts = 0

    async def run(_deployment: Deployment) -> str:
        nonlocal attempts
        attempts += 1
        return "unexpected"

    with pytest.raises(TimeoutError, match="Request deadline exceeded"):
        await manager.execute_with_failover(
            primary,
            "group-a",
            run,
            request_deadline=deadline,
        )

    assert attempts == 0


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
async def test_managed_stream_deadline_precedes_permit_expiry_and_releases_once() -> None:
    class CountingState(RedisStateBackend):
        def __init__(self) -> None:
            super().__init__(redis=None)
            self.lease_ttls: list[int] = []
            self.release_calls = 0

        async def acquire_attempt(  # noqa: ANN001, ANN201
            self, health_ref, capacity, *, lease_ttl_seconds=630
        ):
            self.lease_ttls.append(lease_ttl_seconds)
            return await super().acquire_attempt(
                health_ref,
                capacity,
                lease_ttl_seconds=lease_ttl_seconds,
            )

        async def release_attempt(self, permit):  # noqa: ANN001, ANN201
            self.release_calls += 1
            return await super().release_attempt(permit)

    state = CountingState()
    primary = _deployment("dep-stream-deadline")
    manager = FailoverManager(
        config=FallbackConfig(timeout=0.05),
        candidate_planner=_planner(state, {"group-a": [primary]}),
        state_backend=state,
        cooldown_manager=CooldownManager(state),
    )
    managed = await manager.execute_managed_with_failover(
        primary,
        "group-a",
        lambda _deployment: asyncio.sleep(0, result="stream-open"),
        routing_context={ROUTING_MODE_CONTEXT_KEY: "chat", "metadata": {}},
    )

    async def body():
        await asyncio.Event().wait()
        yield "unreachable"

    async def send(_message):  # noqa: ANN001, ANN202
        return None

    response = DeadlineStreamingResponse(
        body(),
        deadline=managed.deadline,
        close=lambda _exc: managed.release(),
    )
    with pytest.raises(TimeoutError, match="Request deadline exceeded"):
        await response.stream_response(send)

    assert state.lease_ttls == [31]
    assert state.release_calls == 1
    assert await state.get_active_requests(primary.deployment_id) == 0


@pytest.mark.asyncio
async def test_cooldown_manager_ignores_invalid_request_errors():
    state = RedisStateBackend(redis=None)
    tracker = CooldownManager(state_backend=state, allowed_fails=0)

    await tracker.record_failure(
        "dep-a",
        "bad request",
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
@pytest.mark.parametrize("use_keyword", [False, True])
async def test_background_health_checker_accepts_deprecated_state_backend_constructor(
    use_keyword: bool,
):
    state = RedisStateBackend(redis=None)
    deployment = _deployment("dep-legacy-constructor")
    kwargs = {
        "config": HealthCheckConfig(enabled=False),
        "deployment_registry": {"group": [deployment]},
        "checker": lambda _: asyncio.sleep(0, result=HealthProbeResult(healthy=True)),
    }

    with pytest.warns(DeprecationWarning, match="state_backend"):
        if use_keyword:
            checker = BackgroundHealthChecker(**kwargs, state_backend=state)
        else:
            checker = BackgroundHealthChecker(
                kwargs["config"],
                kwargs["deployment_registry"],
                state,
                kwargs["checker"],
            )

    result = await checker.check_deployment_once(deployment)

    assert result.healthy is True
    assert (await state.get_health(deployment.deployment_id))["healthy"] == "true"


@pytest.mark.asyncio
async def test_deprecated_health_checker_constructor_retains_first_failure_threshold():
    state = RedisStateBackend(redis=None)
    deployment = _deployment("dep-legacy-failure")

    with pytest.warns(DeprecationWarning, match="state_backend"):
        checker = BackgroundHealthChecker(
            HealthCheckConfig(enabled=False),
            {"group": [deployment]},
            state,
            lambda _: asyncio.sleep(
                0,
                result=HealthProbeResult(healthy=False, error="provider unavailable"),
            ),
        )

    result = await checker.check_deployment_once(deployment)

    assert result.healthy is False
    assert (await state.get_health(deployment.deployment_id))["healthy"] == "false"


@pytest.mark.asyncio
async def test_background_health_checker_ignores_non_health_affecting_result():
    state = RedisStateBackend(redis=None)
    deployment = _deployment("dep-a")
    checker = BackgroundHealthChecker(
        config=HealthCheckConfig(enabled=False),
        deployment_registry={"group-a": [deployment]},
        health_manager=CooldownManager(state),
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


@pytest.mark.asyncio
async def test_manual_health_check_recovers_expired_cooldown(
    monkeypatch: pytest.MonkeyPatch,
):
    now = {"value": 3_000.0}
    monkeypatch.setattr("src.router.state.time.time", lambda: now["value"])
    state = RedisStateBackend(redis=None)
    deployment = _deployment("dep-manual-recovery")
    cooldown = CooldownManager(state, cooldown_time=1, allowed_fails=0)
    await cooldown.record_failure(deployment.deployment_id, "provider unavailable")
    now["value"] = 3_002.0
    checker = BackgroundHealthChecker(
        config=HealthCheckConfig(enabled=False),
        deployment_registry={"group": [deployment]},
        health_manager=cooldown,
        checker=lambda _: asyncio.sleep(0, result=HealthProbeResult(healthy=True)),
    )

    result = await checker.check_deployment_once(deployment)

    assert result.healthy is True
    health = await state.get_health(deployment.deployment_id)
    assert health["healthy"] == "true"
    assert health["recovery_required"] == "false"
    assert health["consecutive_failures"] == "0"


@pytest.mark.asyncio
async def test_manual_health_probe_claim_is_released_after_completion():
    state = RedisStateBackend(FakeRedis())
    deployment = _deployment("dep-manual")
    calls = 0

    async def check(_candidate: Deployment) -> HealthProbeResult:
        nonlocal calls
        calls += 1
        return HealthProbeResult(healthy=True)

    checker = BackgroundHealthChecker(
        config=HealthCheckConfig(enabled=False),
        deployment_registry={"group": [deployment]},
        health_manager=CooldownManager(state),
        checker=check,
    )

    await checker.check_deployment_once(deployment)
    await checker.check_deployment_once(deployment)

    assert calls == 2


@pytest.mark.asyncio
async def test_manual_recovery_does_not_race_background_recovery():
    redis = FakeRedis()
    state = RedisStateBackend(redis)
    deployment = _deployment("dep-shared-recovery")
    cooldown = CooldownManager(state, cooldown_time=60, allowed_fails=0)
    await cooldown.record_failure(deployment.deployment_id, "provider unavailable")
    await redis.delete(state.keyspace.cooldown(deployment.deployment_id))
    background_started = asyncio.Event()

    async def background_check(_candidate: Deployment) -> HealthProbeResult:
        background_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    background = BackgroundHealthChecker(
        config=HealthCheckConfig(enabled=True, interval_seconds=60),
        deployment_registry={"group": [deployment]},
        health_manager=cooldown,
        checker=background_check,
    )
    manual = BackgroundHealthChecker(
        config=HealthCheckConfig(enabled=False),
        deployment_registry={"group": [deployment]},
        health_manager=cooldown,
        checker=lambda _: asyncio.sleep(0, result=HealthProbeResult(healthy=True)),
    )
    background_task = asyncio.create_task(background._run_coordinated_check(deployment))
    await background_started.wait()

    with pytest.raises(HealthCheckInProgressError):
        await manual.check_deployment_once(deployment)

    background_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await background_task
    recovery = await state.acquire_attempt(deployment.deployment_id, AttemptCapacity())
    assert recovery.acquired is True
    assert recovery.recovery is True
    await state.release_attempt(recovery)


@pytest.mark.asyncio
async def test_background_health_checker_deduplicates_shared_deployments():
    state = RedisStateBackend(redis=None)
    deployment = _deployment("dep-shared")
    calls: list[str] = []

    async def check(candidate: Deployment) -> HealthProbeResult:
        calls.append(candidate.deployment_id)
        return HealthProbeResult(healthy=True)

    checker = BackgroundHealthChecker(
        config=HealthCheckConfig(enabled=True),
        deployment_registry={
            "base-group": [deployment],
            "route-group": [deployment],
        },
        health_manager=CooldownManager(state),
        checker=check,
    )

    await checker._run_health_checks()

    assert calls == [deployment.deployment_id]


@pytest.mark.asyncio
async def test_background_health_check_claim_is_shared_between_replicas():
    state = RedisStateBackend(FakeRedis())
    deployment = _deployment("dep-shared")
    calls: list[str] = []

    async def check(candidate: Deployment) -> HealthProbeResult:
        calls.append(candidate.deployment_id)
        return HealthProbeResult(healthy=True)

    config = HealthCheckConfig(enabled=True, interval_seconds=60)
    first = BackgroundHealthChecker(
        config=config,
        deployment_registry={"group": [deployment]},
        health_manager=CooldownManager(state),
        checker=check,
    )
    second = BackgroundHealthChecker(
        config=config,
        deployment_registry={"group": [deployment]},
        health_manager=CooldownManager(state),
        checker=check,
    )

    await asyncio.gather(first._run_health_checks(), second._run_health_checks())

    assert calls == [deployment.deployment_id]


@pytest.mark.asyncio
async def test_background_health_checker_bounds_scheduled_probe_fanout():
    state = RedisStateBackend(redis=None)
    deployments = [_deployment(f"dep-{index}") for index in range(12)]
    active = 0
    max_active = 0

    async def check(_candidate: Deployment) -> HealthProbeResult:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.001)
        active -= 1
        return HealthProbeResult(healthy=True)

    checker = BackgroundHealthChecker(
        config=HealthCheckConfig(enabled=True, max_concurrency=3),
        deployment_registry={"group": deployments},
        health_manager=CooldownManager(state),
        checker=check,
    )

    await checker._run_health_checks()

    assert max_active == 3


@pytest.mark.asyncio
async def test_background_health_probe_restores_expired_cooldown_without_request_traffic(
    monkeypatch: pytest.MonkeyPatch,
):
    now = {"value": 4_000.0}
    monkeypatch.setattr("src.router.state.time.time", lambda: now["value"])
    state = RedisStateBackend(redis=None)
    deployment = _deployment("dep-recovery")
    cooldown = CooldownManager(state, cooldown_time=1, allowed_fails=0)
    await cooldown.record_failure(deployment.deployment_id, "provider unavailable")
    now["value"] = 4_002.0

    checker = BackgroundHealthChecker(
        config=HealthCheckConfig(enabled=True, interval_seconds=60),
        deployment_registry={"group": [deployment]},
        health_manager=cooldown,
        checker=lambda _: asyncio.sleep(0, result=HealthProbeResult(healthy=True)),
    )
    await checker._run_health_checks()

    health = await state.get_health(deployment.deployment_id)
    assert health["healthy"] == "true"
    assert health["consecutive_failures"] == "0"
    permit = await state.acquire_attempt(deployment.deployment_id, AttemptCapacity())
    assert permit.acquired is True
    assert permit.recovery is False


@pytest.mark.asyncio
async def test_background_recovery_probe_does_not_race_request_half_open(
    monkeypatch: pytest.MonkeyPatch,
):
    now = {"value": 5_000.0}
    monkeypatch.setattr("src.router.state.time.time", lambda: now["value"])
    state = RedisStateBackend(redis=None)
    deployment = _deployment("dep-recovery")
    cooldown = CooldownManager(state, cooldown_time=1, allowed_fails=0)
    await cooldown.record_failure(deployment.deployment_id, "provider unavailable")
    now["value"] = 5_002.0
    request_permit = await state.acquire_attempt(deployment.deployment_id, AttemptCapacity())
    assert request_permit.acquired is True
    assert request_permit.recovery is True
    probe_calls: list[str] = []

    async def check(candidate: Deployment) -> HealthProbeResult:
        probe_calls.append(candidate.deployment_id)
        return HealthProbeResult(healthy=True)

    checker = BackgroundHealthChecker(
        config=HealthCheckConfig(enabled=True, interval_seconds=60),
        deployment_registry={"group": [deployment]},
        health_manager=cooldown,
        checker=check,
    )

    await checker._run_health_checks()
    assert probe_calls == []

    await state.release_attempt(request_permit)
    await checker._run_health_checks()
    assert probe_calls == [deployment.deployment_id]
    assert (await state.get_health(deployment.deployment_id))["healthy"] == "true"


@pytest.mark.asyncio
async def test_health_neutral_background_recovery_releases_half_open_claim(
    monkeypatch: pytest.MonkeyPatch,
):
    now = {"value": 6_000.0}
    monkeypatch.setattr("src.router.state.time.time", lambda: now["value"])
    state = RedisStateBackend(redis=None)
    deployment = _deployment("dep-recovery")
    cooldown = CooldownManager(state, cooldown_time=1, allowed_fails=0)
    await cooldown.record_failure(deployment.deployment_id, "provider unavailable")
    now["value"] = 6_002.0
    checker = BackgroundHealthChecker(
        config=HealthCheckConfig(enabled=True, interval_seconds=60),
        deployment_registry={"group": [deployment]},
        health_manager=cooldown,
        checker=lambda _: asyncio.sleep(
            0,
            result=HealthProbeResult(
                healthy=False,
                error="probe is not supported",
                affects_deployment_health=False,
            ),
        ),
    )

    await checker._run_health_checks()

    health = await state.get_health(deployment.deployment_id)
    assert health["healthy"] == "false"
    permit = await state.acquire_attempt(deployment.deployment_id, AttemptCapacity())
    assert permit.acquired is True
    assert permit.recovery is True


@pytest.mark.asyncio
async def test_cancelled_background_recovery_releases_half_open_claim(
    monkeypatch: pytest.MonkeyPatch,
):
    now = {"value": 7_000.0}
    monkeypatch.setattr("src.router.state.time.time", lambda: now["value"])
    state = RedisStateBackend(redis=None)
    deployment = _deployment("dep-recovery")
    cooldown = CooldownManager(state, cooldown_time=1, allowed_fails=0)
    await cooldown.record_failure(deployment.deployment_id, "provider unavailable")
    now["value"] = 7_002.0
    started = asyncio.Event()

    async def check(_candidate: Deployment) -> HealthProbeResult:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    checker = BackgroundHealthChecker(
        config=HealthCheckConfig(enabled=True, interval_seconds=60),
        deployment_registry={"group": [deployment]},
        health_manager=cooldown,
        checker=check,
    )
    task = asyncio.create_task(checker._run_coordinated_check(deployment))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    permit = await state.acquire_attempt(deployment.deployment_id, AttemptCapacity())
    assert permit.acquired is True
    assert permit.recovery is True


@pytest.mark.asyncio
async def test_health_endpoint_deduplicates_shared_deployments():
    state = RedisStateBackend(redis=None)
    deployment = _deployment("dep-shared")
    handler = HealthEndpointHandler(
        deployment_registry={
            "base-group": [deployment],
            "route-group": [deployment],
        },
        state_backend=state,
    )

    payload = await handler.get_health_status()

    assert payload["total_count"] == 1
    assert [item["deployment_id"] for item in payload["deployments"]] == [deployment.deployment_id]


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
