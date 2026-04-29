from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from src.models.errors import (
    InvalidRequestError,
    NO_HEALTHY_DEPLOYMENTS_CODE,
    RateLimitError,
    ServiceUnavailableError,
    TimeoutError,
    parse_retry_after_header,
)
from src.router import CooldownManager, FallbackConfig, FailoverManager, PassiveHealthTracker, RedisStateBackend
from src.router.health_policy import affects_deployment_health
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
        (ServiceUnavailableError(message="provider unavailable", affects_deployment_health=True), True),
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
        deployment_registry={"group-a": [primary]},
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
        deployment_registry={"group-a": [primary, fallback]},
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
        deployment_registry={"group-a": [primary]},
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
        deployment_registry={"group-a": [primary]},
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
        deployment_registry={"group-a": [primary]},
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
        deployment_registry={"group-a": [primary]},
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
        deployment_registry={"group-a": [primary]},
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
        deployment_registry={"group-a": [primary, fallback]},
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
        deployment_registry={"group-a": [primary], "ctx-fallbacks": [fallback_a, fallback_b]},
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
        deployment_registry={"group-a": [primary], "ctx-fallbacks": [fallback_a, fallback_b]},
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
@pytest.mark.parametrize(
    ("status_code", "headers"),
    [
        (429, {"Retry-After": "7"}),
        (503, {}),
    ],
)
async def test_failover_retries_transient_raw_http_errors(status_code: int, headers: dict[str, str]):
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
        deployment_registry={"group-a": [primary]},
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
