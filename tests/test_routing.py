from __future__ import annotations

import asyncio

import pytest

from src.models.errors import (
    ModelNotFoundError,
    NO_HEALTHY_DEPLOYMENTS_CODE,
    ServiceUnavailableError,
)
import src.router.strategies as strategies_module
from src.router import (
    AttemptCapacity,
    AttemptCapacityLimit,
    AttemptRejectionReason,
    CooldownManager,
    HealthEndpointHandler,
    RedisStateBackend,
    RouterRedisKeyspace,
    Router,
    RouterConfig,
    RoutingStrategy,
    build_deployment_registry,
    build_route_group_policies,
)
from src.router.health_state import (
    DeploymentHealthRef,
    FAILURE_WINDOW_SECONDS,
    HEALTH_STATE_RETENTION_SECONDS,
)
from src.router.router import Deployment
from src.router.usage import normalize_router_usage
from tests.conftest import FakeRedis


@pytest.mark.asyncio
async def test_deployments_health_endpoint(client):
    response = await client.get("/health/deployments")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"healthy", "degraded"}
    assert payload["total_count"] >= 1
    assert isinstance(payload["deployments"], list)


@pytest.mark.asyncio
async def test_least_busy_strategy_selects_lowest_active_requests():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {"deployment_id": "dep-a", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
                {"deployment_id": "dep-b", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
            ]
        }
    )
    router = Router(
        strategy=RoutingStrategy.LEAST_BUSY,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    permit = await state.acquire_attempt("dep-a", AttemptCapacity())
    try:
        selected = await router.select_deployment("gpt-4o-mini", {})
        assert selected is not None
        assert selected.deployment_id == "dep-b"
    finally:
        await state.release_attempt(permit)


@pytest.mark.asyncio
async def test_request_tags_filter_candidates_before_strategy(monkeypatch):
    monkeypatch.setattr(strategies_module.random, "choice", lambda deployments: deployments[-1])
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-tagged",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"tags": ["vip"]},
                },
                {
                    "deployment_id": "dep-untagged",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"tags": []},
                },
            ]
        }
    )
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("gpt-4o-mini", {"metadata": {"tags": ["vip"]}})

    assert selected is not None
    assert selected.deployment_id == "dep-tagged"


@pytest.mark.asyncio
async def test_tag_based_strategy_applies_tag_filtering():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-tagged",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"tags": ["vip"]},
                },
                {
                    "deployment_id": "dep-untagged",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                },
            ]
        }
    )
    router = Router(
        strategy=RoutingStrategy.TAG_BASED,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("gpt-4o-mini", {"metadata": {"tags": ["vip"]}})

    assert selected is not None
    assert selected.deployment_id == "dep-tagged"


def test_tag_based_strategy_is_weighted_selection_on_prefiltered_pool():
    strategy = strategies_module.TagBasedStrategy()
    assert isinstance(strategy.fallback, strategies_module.WeightedStrategy)


@pytest.mark.asyncio
async def test_priority_based_strategy_applies_priority_filtering(monkeypatch):
    monkeypatch.setattr(strategies_module.random, "choice", lambda deployments: deployments[-1])
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-primary",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"priority": 0},
                },
                {
                    "deployment_id": "dep-secondary",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"priority": 10},
                },
            ]
        }
    )
    simple_router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )
    priority_router = Router(
        strategy=RoutingStrategy.PRIORITY_BASED,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    simple_selected = await simple_router.select_deployment("gpt-4o-mini", {})
    priority_selected = await priority_router.select_deployment("gpt-4o-mini", {})

    assert simple_selected is not None
    assert simple_selected.deployment_id == "dep-secondary"
    assert priority_selected is not None
    assert priority_selected.deployment_id == "dep-primary"


@pytest.mark.asyncio
async def test_latency_based_strategy_keeps_unsampled_members_eligible(monkeypatch):
    monkeypatch.setattr(
        strategies_module.random, "uniform", lambda start, end: (start + end) * 0.75
    )
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-sampled",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                },
                {
                    "deployment_id": "dep-unsampled",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                },
            ]
        }
    )
    await state.record_latency("dep-sampled", 10.0)
    router = Router(
        strategy=RoutingStrategy.LATENCY_BASED,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("gpt-4o-mini", {})

    assert selected is not None
    assert selected.deployment_id == "dep-unsampled"


@pytest.mark.asyncio
async def test_cost_based_strategy_uses_mode_specific_pricing():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "image-group": [
                {
                    "deployment_id": "dep-expensive",
                    "deltallm_params": {"model": "openai/image"},
                    "model_info": {"mode": "image_generation", "input_cost_per_image": 0.10},
                },
                {
                    "deployment_id": "dep-cheap",
                    "deltallm_params": {"model": "openai/image"},
                    "model_info": {"mode": "image_generation", "input_cost_per_image": 0.02},
                },
            ]
        }
    )
    router = Router(
        strategy=RoutingStrategy.COST_BASED,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("image-group", {})

    assert selected is not None
    assert selected.deployment_id == "dep-cheap"


@pytest.mark.asyncio
async def test_usage_based_strategy_uses_router_state_usage():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-a",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"rpm_limit": 10, "tpm_limit": 100},
                },
                {
                    "deployment_id": "dep-b",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"rpm_limit": 10, "tpm_limit": 100},
                },
            ]
        }
    )
    await state.increment_usage("dep-a", 80)
    await state.increment_usage("dep-a", 0)
    await state.increment_usage("dep-b", 5)
    router = Router(
        strategy=RoutingStrategy.USAGE_BASED,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("gpt-4o-mini", {})

    assert selected is not None
    assert selected.deployment_id == "dep-b"


@pytest.mark.asyncio
async def test_cooldown_batch_uses_local_fallback_state():
    state = RedisStateBackend(redis=None)

    await CooldownManager(state).manual_cooldown("dep-a", 30, "manual")

    cooldowns = await state.get_cooldown_batch(["dep-a", "dep-b"])

    assert cooldowns == {"dep-a": True, "dep-b": False}


@pytest.mark.asyncio
async def test_redis_health_transitions_apply_bounded_hash_ttls() -> None:
    redis = FakeRedis()
    state = RedisStateBackend(redis)
    cooldown = CooldownManager(state, cooldown_time=60, allowed_fails=0)

    await cooldown.record_success("healthy")
    await cooldown.record_failure("failed", "provider unavailable")
    long_cooldown = HEALTH_STATE_RETENTION_SECONDS + 10
    await cooldown.manual_cooldown("manual", long_cooldown, "operator hold")

    assert await redis.ttl(state.keyspace.health("healthy")) == HEALTH_STATE_RETENTION_SECONDS
    assert await redis.ttl(state.keyspace.health("failed")) == HEALTH_STATE_RETENTION_SECONDS
    assert await redis.ttl(state.keyspace.health("manual")) == (
        long_cooldown + FAILURE_WINDOW_SECONDS
    )


@pytest.mark.asyncio
async def test_health_invalidation_deletes_exact_health_keys_in_one_redis_call() -> None:
    class CountingRedis(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.delete_calls: list[tuple[str, ...]] = []

        async def delete(self, *keys: str):
            self.delete_calls.append(keys)
            await super().delete(*keys)

    redis = CountingRedis()
    state = RedisStateBackend(redis)
    deployment_id = "dep-replaced"
    unrelated_id = "dep-current"

    def health_keys(item: str) -> tuple[str, ...]:
        return (
            state.keyspace.health_failures(item),
            state.keyspace.health(item),
            state.keyspace.cooldown(item),
            state.keyspace.health_recovery(item),
            state.keyspace.health_probe(item, "background"),
            state.keyspace.health_probe(item, "manual"),
        )

    for item in (deployment_id, unrelated_id):
        failures, health, *string_keys = health_keys(item)
        await redis.zadd(failures, {"failure": 1})
        await redis.hset(health, mapping={"healthy": "false"})
        for key in string_keys:
            await redis.set(key, "owner", ex=60)

    preserved_keys = (
        state.keyspace.active_requests(deployment_id),
        state.keyspace.attempt_owners(deployment_id),
        state.keyspace.latency(deployment_id),
        state.keyspace.usage(deployment_id, "requests", "123"),
    )
    for key in preserved_keys:
        await redis.set(key, "1", ex=60)

    assert await state.invalidate_health_state([deployment_id, deployment_id]) is True

    assert len(redis.delete_calls) == 1
    assert set(redis.delete_calls[0]) == set(health_keys(deployment_id))
    for key in health_keys(deployment_id):
        assert key not in redis.store
        assert key not in redis.hash_store
        assert key not in redis.zset_store
    for key in health_keys(unrelated_id):
        assert key in redis.store or key in redis.hash_store or key in redis.zset_store
    for key in preserved_keys:
        assert key in redis.store


@pytest.mark.asyncio
async def test_health_invalidation_is_generation_exact_for_reused_deployment_id() -> None:
    redis = FakeRedis()
    state = RedisStateBackend(redis)
    retired = DeploymentHealthRef("dep-reused", "retired")
    current = DeploymentHealthRef("dep-reused", "current")

    for item in (retired, current):
        await redis.hset(
            state.keyspace.health(item.deployment_id, item.generation),
            mapping={"healthy": "false"},
        )
        await redis.set(
            state.keyspace.cooldown(item.deployment_id, item.generation),
            "provider unavailable",
            ex=60,
        )

    assert await state.invalidate_health_state([retired]) is True

    assert await state.get_health(retired) == {}
    assert (await state.get_health(current))["healthy"] == "false"
    assert await state.is_cooled_down(current) is True


@pytest.mark.asyncio
async def test_health_invalidation_chunks_control_plane_redis_work() -> None:
    class CountingRedis(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.delete_calls: list[tuple[str, ...]] = []

        async def delete(self, *keys: str):
            self.delete_calls.append(keys)
            return await super().delete(*keys)

    redis = CountingRedis()
    state = RedisStateBackend(redis)
    health_refs = [DeploymentHealthRef(f"dep-{index}", "generation") for index in range(101)]

    assert await state.invalidate_health_state(health_refs) is True

    assert [len(keys) for keys in redis.delete_calls] == [600, 6]


@pytest.mark.asyncio
async def test_health_invalidation_caps_control_plane_redis_work() -> None:
    class CountingRedis(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.delete_calls: list[tuple[str, ...]] = []

        async def delete(self, *keys: str):
            self.delete_calls.append(keys)
            return await super().delete(*keys)

    redis = CountingRedis()
    state = RedisStateBackend(redis)
    health_refs = [DeploymentHealthRef(f"dep-{index}", "generation") for index in range(1_001)]

    assert await state.invalidate_health_state(health_refs) is False

    assert len(redis.delete_calls) == 10
    assert all(len(keys) == 600 for keys in redis.delete_calls)


@pytest.mark.asyncio
async def test_health_invalidation_clears_local_health_but_preserves_admission_state() -> None:
    state = RedisStateBackend(redis=None, degraded_mode="fail_open")
    cooldown = CooldownManager(state, cooldown_time=60, allowed_fails=0)
    permit = await state.acquire_attempt("dep-replaced", AttemptCapacity())
    await cooldown.record_failure("dep-replaced", "provider unavailable")

    assert await state.invalidate_health_state(["dep-replaced"]) is False

    assert await state.get_health("dep-replaced") == {}
    assert await state.is_cooled_down("dep-replaced") is False
    assert await state.get_active_requests("dep-replaced") == 1
    await state.release_attempt(permit)


def test_deployment_health_generation_changes_only_for_provider_identity() -> None:
    base = Deployment(
        deployment_id="dep-generation",
        model_name="group",
        deltallm_params={"model": "openai/a", "api_key": "secret-value"},
        model_info={"weight": 1},
        health_incarnation="incarnation-a",
        named_credential_id="credential-a",
    )
    metadata_update = Deployment(
        deployment_id="dep-generation",
        model_name="group",
        deltallm_params={"model": "openai/a", "api_key": "secret-value"},
        model_info={"weight": 10},
        health_incarnation="incarnation-a",
        named_credential_id="credential-a",
    )
    provider_update = Deployment(
        deployment_id="dep-generation",
        model_name="group",
        deltallm_params={"model": "openai/b", "api_key": "secret-value"},
        health_incarnation="incarnation-a",
        named_credential_id="credential-a",
    )
    recreated = Deployment(
        deployment_id="dep-generation",
        model_name="group",
        deltallm_params={"model": "openai/a", "api_key": "secret-value"},
        health_incarnation="incarnation-b",
        named_credential_id="credential-a",
    )

    assert base.health_ref == metadata_update.health_ref
    assert base.health_ref != provider_update.health_ref
    assert base.health_ref != recreated.health_ref
    assert "secret-value" not in base.health_ref.generation


@pytest.mark.asyncio
async def test_retired_generation_outcome_cannot_change_replacement_health() -> None:
    state = RedisStateBackend(redis=None, degraded_mode="fail_open")
    cooldown = CooldownManager(state, cooldown_time=60, allowed_fails=0)
    old = Deployment(
        deployment_id="dep-reused",
        model_name="group",
        deltallm_params={"model": "openai/old"},
        health_incarnation="incarnation-old",
    )
    replacement = Deployment(
        deployment_id="dep-reused",
        model_name="group",
        deltallm_params={"model": "openai/new"},
        health_incarnation="incarnation-old",
    )

    permit = await state.acquire_attempt(old.health_ref, AttemptCapacity())
    assert permit.acquired is True
    assert await state.invalidate_health_state([old.health_ref]) is False

    await cooldown.record_failure(old.health_ref, "late provider failure")

    assert (await state.get_health(old.health_ref))["healthy"] == "false"
    assert await state.get_health(replacement.health_ref) == {}
    assert await state.is_cooled_down(replacement.health_ref) is False
    await state.release_attempt(permit)


@pytest.mark.asyncio
async def test_probe_claims_are_fenced_by_health_generation() -> None:
    redis = FakeRedis()
    state = RedisStateBackend(redis)
    old = DeploymentHealthRef("dep-reused", "old-generation")
    replacement = DeploymentHealthRef("dep-reused", "new-generation")

    old_claim = await state.claim_health_probe(old, 60)
    duplicate_old_claim = await state.claim_health_probe(old, 60)
    replacement_claim = await state.claim_health_probe(replacement, 60)

    assert old_claim is not None
    assert duplicate_old_claim is None
    assert replacement_claim is not None
    assert old_claim.health_ref == old
    assert replacement_claim.health_ref == replacement
    await state.release_health_probe(old, old_claim)
    await state.release_health_probe(replacement, replacement_claim)


@pytest.mark.asyncio
async def test_expired_local_cooldown_allows_one_half_open_attempt(
    monkeypatch: pytest.MonkeyPatch,
):
    now = {"value": 1_000.0}
    monkeypatch.setattr("src.router.state.time.time", lambda: now["value"])
    monkeypatch.setattr("src.router.cooldown.time.time", lambda: now["value"])
    state = RedisStateBackend(redis=None, degraded_mode="fail_open")
    cooldown = CooldownManager(state, cooldown_time=1, allowed_fails=0)

    assert await cooldown.record_failure("dep-a", "provider unavailable") is True
    assert await state.is_cooled_down("dep-a") is True

    now["value"] = 1_002.0
    permits = await asyncio.gather(
        *(state.acquire_attempt("dep-a", AttemptCapacity()) for _ in range(8))
    )

    acquired = [permit for permit in permits if permit.acquired]
    rejected = [permit for permit in permits if not permit.acquired]
    assert len(acquired) == 1
    assert acquired[0].recovery is True
    assert {permit.rejection_reason for permit in rejected} == {
        AttemptRejectionReason.RECOVERY_IN_PROGRESS
    }

    await cooldown.record_success("dep-a", recovery_token=acquired[0].owner_token)
    await state.release_attempt(acquired[0])

    health = await state.get_health("dep-a")
    assert health["healthy"] == "true"
    assert health["consecutive_failures"] == "0"
    ordinary = await state.acquire_attempt("dep-a", AttemptCapacity())
    assert ordinary.acquired is True
    assert ordinary.recovery is False
    await state.release_attempt(ordinary)


@pytest.mark.asyncio
async def test_failed_local_half_open_attempt_reenters_cooldown(
    monkeypatch: pytest.MonkeyPatch,
):
    now = {"value": 2_000.0}
    monkeypatch.setattr("src.router.state.time.time", lambda: now["value"])
    monkeypatch.setattr("src.router.cooldown.time.time", lambda: now["value"])
    state = RedisStateBackend(redis=None, degraded_mode="fail_open")
    cooldown = CooldownManager(state, cooldown_time=1, allowed_fails=0)
    await cooldown.record_failure("dep-a", "first failure")

    now["value"] = 2_002.0
    permit = await state.acquire_attempt("dep-a", AttemptCapacity())
    assert permit.acquired is True
    assert permit.recovery is True

    assert (
        await cooldown.record_failure(
            "dep-a",
            "recovery failed",
            recovery_token=permit.owner_token,
        )
        is True
    )
    await state.release_attempt(permit)

    assert await state.is_cooled_down("dep-a") is True
    rejected = await state.acquire_attempt("dep-a", AttemptCapacity())
    assert rejected.rejection_reason == AttemptRejectionReason.COOLDOWN


@pytest.mark.asyncio
async def test_failed_local_half_open_attempt_reenters_cooldown_after_failure_window_expires(
    monkeypatch: pytest.MonkeyPatch,
):
    now = {"value": 3_000.0}
    monkeypatch.setattr("src.router.state.time.time", lambda: now["value"])
    monkeypatch.setattr("src.router.cooldown.time.time", lambda: now["value"])
    state = RedisStateBackend(redis=None, degraded_mode="fail_open")
    cooldown = CooldownManager(state, cooldown_time=301, allowed_fails=2)

    await cooldown.manual_cooldown("dep-a", 301, "operator cooldown")
    now["value"] = 3_302.0
    permit = await state.acquire_attempt("dep-a", AttemptCapacity())
    assert permit.acquired is True
    assert permit.recovery is True

    assert (
        await cooldown.record_failure(
            "dep-a",
            "recovery failed",
            recovery_token=permit.owner_token,
        )
        is True
    )

    health = await state.get_health("dep-a")
    assert health["healthy"] == "false"
    assert health["consecutive_failures"] == "1"
    assert await state.is_cooled_down("dep-a") is True


@pytest.mark.asyncio
async def test_expired_local_half_open_result_is_fenced(
    monkeypatch: pytest.MonkeyPatch,
):
    now = {"value": 4_000.0}
    monkeypatch.setattr("src.router.state.time.time", lambda: now["value"])
    state = RedisStateBackend(redis=None, degraded_mode="fail_open")
    cooldown = CooldownManager(state, cooldown_time=1, allowed_fails=0)
    await cooldown.record_failure("dep-a", "provider unavailable")

    now["value"] = 4_002.0
    permit = await state.acquire_attempt(
        "dep-a",
        AttemptCapacity(),
        lease_ttl_seconds=1,
    )
    assert permit.acquired is True
    assert permit.recovery is True

    now["value"] = 4_004.0
    await cooldown.record_success("dep-a", recovery_token=permit.owner_token)

    health = await state.get_health("dep-a")
    assert health["healthy"] == "false"
    next_permit = await state.acquire_attempt("dep-a", AttemptCapacity())
    assert next_permit.acquired is True
    assert next_permit.recovery is True


@pytest.mark.asyncio
async def test_active_manual_cooldown_is_not_cleared_by_inflight_success(
    monkeypatch: pytest.MonkeyPatch,
):
    now = {"value": 5_000.0}
    monkeypatch.setattr("src.router.state.time.time", lambda: now["value"])
    state = RedisStateBackend(redis=None, degraded_mode="fail_open")
    cooldown = CooldownManager(state)
    await cooldown.manual_cooldown("dep-a", 60, "operator hold")

    await cooldown.record_success("dep-a")

    health = await state.get_health("dep-a")
    assert health["healthy"] == "false"
    assert health["cooldown_kind"] == "manual"
    assert await state.is_cooled_down("dep-a") is True

    now["value"] = 5_061.0
    await cooldown.record_success("dep-a")

    health = await state.get_health("dep-a")
    assert health["healthy"] == "false"
    assert health["cooldown_kind"] == "manual"
    assert await state.is_cooled_down("dep-a") is False

    recovery = await state.acquire_attempt("dep-a", AttemptCapacity())
    assert recovery.acquired is True
    assert recovery.recovery is True
    await cooldown.record_success("dep-a", recovery_token=recovery.owner_token)

    health = await state.get_health("dep-a")
    assert health["healthy"] == "true"
    assert "cooldown_kind" not in health


@pytest.mark.asyncio
async def test_inflight_tokenless_outcomes_cannot_override_automatic_cooldown(
    monkeypatch: pytest.MonkeyPatch,
):
    now = {"value": 5_500.0}
    monkeypatch.setattr("src.router.state.time.time", lambda: now["value"])
    state = RedisStateBackend(redis=None, degraded_mode="fail_open")
    cooldown = CooldownManager(state, cooldown_time=1, allowed_fails=0)

    await cooldown.record_failure("dep-a", "provider unavailable")
    await cooldown.record_success("dep-a")
    await cooldown.record_failure("dep-a", "stale in-flight failure")

    health = await state.get_health("dep-a")
    assert health["healthy"] == "false"
    assert health["consecutive_failures"] == "1"

    now["value"] = 5_502.0
    await cooldown.record_success("dep-a")
    health = await state.get_health("dep-a")
    assert health["healthy"] == "false"


@pytest.mark.asyncio
async def test_router_state_keys_are_isolated_by_environment():
    redis = FakeRedis()
    staging_keys = RouterRedisKeyspace(environment="staging")
    production_keys = RouterRedisKeyspace(environment="production")
    first = RedisStateBackend(
        redis,
        keyspace=staging_keys,
    )
    second = RedisStateBackend(
        redis,
        keyspace=production_keys,
    )

    first_claim = await first.claim_health_probe("dep:a", 60)
    second_claim = await second.claim_health_probe("dep:a", 60)
    await CooldownManager(first, allowed_fails=0).record_failure("dep:a", "staging failure")
    production_permit = await second.acquire_attempt("dep:a", AttemptCapacity())

    assert first_claim is not None
    assert second_claim is not None
    assert staging_keys.health_probe("dep:a", "background") in redis.store
    assert production_keys.health_probe("dep:a", "background") in redis.store
    assert (await first.get_health("dep:a"))["healthy"] == "false"
    assert await first.is_cooled_down("dep:a") is True
    assert await second.get_health("dep:a") == {}
    assert await second.is_cooled_down("dep:a") is False
    assert production_permit.acquired is True
    assert production_permit.recovery is False
    assert staging_keys.health("dep:a") != production_keys.health("dep:a")
    assert staging_keys.cooldown("dep:a") != production_keys.cooldown("dep:a")
    assert staging_keys.active_requests("dep:a") != production_keys.active_requests("dep:a")
    await second.release_attempt(production_permit)


@pytest.mark.asyncio
async def test_redis_state_batch_reads_use_one_round_trip_per_metric(test_app, monkeypatch):
    redis = test_app.state.redis
    state = RedisStateBackend(redis)
    await state.increment_usage("dep-a", 7)
    await state.record_latency("dep-a", 12.5)
    permit = await state.acquire_attempt("dep-a", AttemptCapacity())
    await redis.hset(state.keyspace.health("dep-a"), mapping={"healthy": "false"})

    calls = {"eval": 0, "mget": 0, "pipeline": 0}
    original_eval = redis.eval
    original_mget = redis.mget
    original_pipeline = redis.pipeline

    async def counting_eval(script, numkeys, *args):  # noqa: ANN001, ANN202
        calls["eval"] += 1
        return await original_eval(script, numkeys, *args)

    async def counting_mget(keys):  # noqa: ANN001, ANN202
        calls["mget"] += 1
        return await original_mget(keys)

    def counting_pipeline():  # noqa: ANN202
        calls["pipeline"] += 1
        return original_pipeline()

    monkeypatch.setattr(redis, "eval", counting_eval)
    monkeypatch.setattr(redis, "mget", counting_mget)
    monkeypatch.setattr(redis, "pipeline", counting_pipeline)

    active = await state.get_active_requests_batch(["dep-a", "dep-b"])
    usage = await state.get_usage_batch(["dep-a", "dep-b"])
    health = await state.get_health_batch(["dep-a", "dep-b"])
    latency = await state.get_latency_windows_batch(["dep-a", "dep-b"], 300_000)

    assert calls == {"eval": 1, "mget": 1, "pipeline": 2}
    assert active == {"dep-a": 1, "dep-b": 0}
    assert usage["dep-a"] == {"rpm": 1, "tpm": 7}
    assert usage["dep-b"] == {"rpm": 0, "tpm": 0}
    assert health["dep-a"]["healthy"] == "false"
    assert health["dep-b"] == {}
    assert len(latency["dep-a"]) == 1
    assert latency["dep-b"] == []
    await state.release_attempt(permit)


@pytest.mark.asyncio
async def test_attempt_admission_uses_one_atomic_redis_call_and_owned_release():
    class CountingRedis(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.attempt_eval_calls = 0

        async def eval(self, script, numkeys, *args):  # noqa: ANN001, ANN201
            if "router_attempt_admission_v2" in script:
                self.attempt_eval_calls += 1
            return await super().eval(script, numkeys, *args)

    redis = CountingRedis()
    state = RedisStateBackend(redis)
    capacity = AttemptCapacity((AttemptCapacityLimit("rpm", 1),))

    permit = await state.acquire_attempt("dep-a", capacity)

    assert permit.acquired is True
    assert permit.backend == "redis"
    assert permit.owner_token
    assert permit.expires_at_ms
    assert permit.active_requests == 1
    assert redis.attempt_eval_calls == 1
    assert redis.ttl_store[state.keyspace.active_requests("dep-a")] > 0
    assert redis.ttl_store[state._attempt_owners_key("dep-a")] > 0
    assert await state.get_active_requests("dep-a") == 1

    released = await state.release_attempt(permit)

    assert released == 0
    assert await state.release_attempt(permit) == 0
    assert await state.get_active_requests("dep-a") == 0


@pytest.mark.parametrize(
    ("state_change", "expected_reason"),
    [
        ("cooldown", AttemptRejectionReason.COOLDOWN),
        ("unhealthy", AttemptRejectionReason.UNHEALTHY),
        ("capacity", AttemptRejectionReason.CAPACITY),
    ],
)
@pytest.mark.asyncio
async def test_attempt_admission_rejects_dynamic_state_without_incrementing_active(
    state_change: str,
    expected_reason: AttemptRejectionReason,
):
    redis = FakeRedis()
    state = RedisStateBackend(redis)
    capacity = AttemptCapacity((AttemptCapacityLimit("rpm", 1),))
    if state_change == "cooldown":
        await CooldownManager(state).manual_cooldown("dep-a", 30, "manual")
    elif state_change == "unhealthy":
        await redis.hset(state.keyspace.health("dep-a"), mapping={"healthy": "false"})
    else:
        await state.increment_usage("dep-a", 0)

    permit = await state.acquire_attempt("dep-a", capacity)

    assert permit.acquired is False
    assert permit.rejection_reason == expected_reason
    assert state.keyspace.active_requests("dep-a") not in redis.store
    assert await state.release_attempt(permit) == 0
    assert state.keyspace.active_requests("dep-a") not in redis.store


@pytest.mark.asyncio
async def test_local_attempt_permit_releases_locally_after_redis_recovery():
    class FailingOnceRedis(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.fail_next_attempt = True

        async def eval(self, script, numkeys, *args):  # noqa: ANN001, ANN201
            if "router_attempt_admission_v2" in script and self.fail_next_attempt:
                self.fail_next_attempt = False
                raise RuntimeError("redis unavailable")
            return await super().eval(script, numkeys, *args)

    redis = FailingOnceRedis()
    state = RedisStateBackend(redis, degraded_mode="fail_open")

    local_permit = await state.acquire_attempt("dep-a", AttemptCapacity())

    assert local_permit.acquired is True
    assert local_permit.backend == "local"
    assert state.get_backend_status()["mode"] == "degraded"
    assert await state.release_attempt(local_permit) == 0
    assert state.keyspace.active_requests("dep-a") not in redis.store

    redis_permit = await state.acquire_attempt("dep-a", AttemptCapacity())

    assert redis_permit.acquired is True
    assert redis_permit.backend == "redis"
    assert state.get_backend_status()["mode"] == "redis"
    assert await state.release_attempt(redis_permit) == 0


@pytest.mark.asyncio
async def test_attempt_release_is_owner_guarded_and_idempotent():
    state = RedisStateBackend(FakeRedis())
    first = await state.acquire_attempt("dep-a", AttemptCapacity())
    second = await state.acquire_attempt("dep-a", AttemptCapacity())

    assert first.owner_token != second.owner_token
    assert await state.release_attempt(first) == 1
    assert await state.release_attempt(first) == 1
    assert await state.get_active_requests("dep-a") == 1
    assert await state.release_attempt(second) == 0


@pytest.mark.asyncio
async def test_attempt_release_outage_recovers_from_expiring_owner_state():
    class ReleaseOutageRedis(FakeRedis):
        fail_release = False

        async def eval(self, script, numkeys, *args):  # noqa: ANN001, ANN201
            if self.fail_release and "router_attempt_release_v2" in script:
                raise RuntimeError("redis unavailable during release")
            return await super().eval(script, numkeys, *args)

    redis = ReleaseOutageRedis()
    state = RedisStateBackend(redis, degraded_mode="fail_open")
    permit = await state.acquire_attempt(
        "dep-a",
        AttemptCapacity(),
        lease_ttl_seconds=1,
    )
    redis.fail_release = True

    assert await state.release_attempt(permit) is None
    assert state.get_backend_status()["mode"] == "degraded"
    assert redis.ttl_store[state.keyspace.active_requests("dep-a")] > 0

    redis.fail_release = False
    owners_key = state._attempt_owners_key("dep-a")
    redis.zset_store[owners_key] = [(0, str(permit.owner_token))]

    assert await state.get_active_requests("dep-a") == 0
    recovered = await state.acquire_attempt("dep-a", AttemptCapacity())
    assert recovered.active_requests == 1
    assert state.get_backend_status()["mode"] == "redis"
    assert await state.release_attempt(recovered) == 0


@pytest.mark.asyncio
async def test_local_attempt_permit_expires_without_release(monkeypatch: pytest.MonkeyPatch):
    now = {"value": 1_000.0}
    monkeypatch.setattr("src.router.state.time.time", lambda: now["value"])
    state = RedisStateBackend(redis=None, degraded_mode="fail_open")

    permit = await state.acquire_attempt(
        "dep-a",
        AttemptCapacity(),
        lease_ttl_seconds=1,
    )
    assert permit.active_requests == 1

    now["value"] = 1_002.0
    assert await state.get_active_requests("dep-a") == 0
    assert "dep-a" not in state._active_permits


@pytest.mark.parametrize(
    ("mode", "counter", "limit_field"),
    [
        ("chat", "tpm", "tpm_limit"),
        ("embedding", "tpm", "tpm_limit"),
        ("image_generation", "image_pm", "image_pm_limit"),
        ("audio_speech", "audio_seconds_pm", "audio_seconds_pm_limit"),
        ("audio_transcription", "char_pm", "char_pm_limit"),
        ("rerank", "rerank_units_pm", "rerank_units_pm_limit"),
    ],
)
@pytest.mark.asyncio
async def test_attempt_capacity_revalidation_uses_workload_specific_counter(
    mode: str,
    counter: str,
    limit_field: str,
):
    state = RedisStateBackend(FakeRedis())
    registry = build_deployment_registry(
        {
            "model-group": [
                {
                    "deployment_id": "dep-a",
                    "deltallm_params": {
                        "provider": "vllm",
                        "model": "vllm/test-model",
                    },
                    "model_info": {
                        "mode": mode,
                        limit_field: 1,
                    },
                }
            ]
        }
    )
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state,
        config=RouterConfig(enable_pre_call_checks=True),
        deployment_registry=registry,
    )
    routing_context = {"routing_mode": mode, "metadata": {}}
    selected = await router.select_deployment("model-group", routing_context)
    assert selected is not None
    await state.increment_usage_counters(selected.deployment_id, {counter: 1})

    permit = await router.acquire_attempt(selected, routing_context)

    assert permit.acquired is False
    assert permit.rejection_reason == AttemptRejectionReason.CAPACITY


def test_require_deployment_raises_service_unavailable_when_group_exists_but_none_healthy():
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=RedisStateBackend(redis=None),
        config=RouterConfig(),
        deployment_registry=build_deployment_registry(
            {
                "gpt-4o-mini": [
                    {"deployment_id": "dep-a", "deltallm_params": {"model": "openai/gpt-4o-mini"}}
                ]
            }
        ),
    )

    with pytest.raises(ServiceUnavailableError) as exc_info:
        router.require_deployment("gpt-4o-mini", None)

    assert exc_info.value.code == NO_HEALTHY_DEPLOYMENTS_CODE
    assert exc_info.value.status_code == 503


def test_require_deployment_raises_model_not_found_for_missing_group():
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=RedisStateBackend(redis=None),
        config=RouterConfig(),
        deployment_registry=build_deployment_registry({}),
    )

    with pytest.raises(ModelNotFoundError) as exc_info:
        router.require_deployment("missing-model", None)

    assert exc_info.value.code == "model_not_found"
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_usage_based_strategy_uses_image_limits_when_configured():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "image-group": [
                {
                    "deployment_id": "dep-hot",
                    "deltallm_params": {"model": "openai/image"},
                    "model_info": {
                        "mode": "image_generation",
                        "rpm_limit": 10,
                        "image_pm_limit": 10,
                    },
                },
                {
                    "deployment_id": "dep-cool",
                    "deltallm_params": {"model": "openai/image"},
                    "model_info": {
                        "mode": "image_generation",
                        "rpm_limit": 10,
                        "image_pm_limit": 10,
                    },
                },
            ]
        }
    )
    await state.increment_usage_counters("dep-hot", {"rpm": 1, "image_pm": 9})
    await state.increment_usage_counters("dep-cool", {"rpm": 1, "image_pm": 1})
    router = Router(
        strategy=RoutingStrategy.USAGE_BASED,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("image-group", {})

    assert selected is not None
    assert selected.deployment_id == "dep-cool"


@pytest.mark.asyncio
async def test_rate_limit_aware_strategy_skips_hot_deployments():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-hot",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"rpm_limit": 10, "tpm_limit": 100},
                },
                {
                    "deployment_id": "dep-cool",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"rpm_limit": 10, "tpm_limit": 100},
                },
            ]
        }
    )
    for _ in range(9):
        await state.increment_usage("dep-hot", 0)
    await state.increment_usage("dep-hot", 95)
    router = Router(
        strategy=RoutingStrategy.RATE_LIMIT_AWARE,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("gpt-4o-mini", {})

    assert selected is not None
    assert selected.deployment_id == "dep-cool"


@pytest.mark.asyncio
async def test_rate_limit_aware_strategy_uses_audio_limits_when_configured():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "audio-group": [
                {
                    "deployment_id": "dep-hot",
                    "deltallm_params": {"model": "openai/audio"},
                    "model_info": {
                        "mode": "audio_transcription",
                        "rpm_limit": 10,
                        "audio_seconds_pm_limit": 10,
                    },
                },
                {
                    "deployment_id": "dep-cool",
                    "deltallm_params": {"model": "openai/audio"},
                    "model_info": {
                        "mode": "audio_transcription",
                        "rpm_limit": 10,
                        "audio_seconds_pm_limit": 10,
                    },
                },
            ]
        }
    )
    await state.increment_usage_counters("dep-hot", {"rpm": 1, "audio_seconds_pm": 9})
    await state.increment_usage_counters("dep-cool", {"rpm": 1, "audio_seconds_pm": 1})
    router = Router(
        strategy=RoutingStrategy.RATE_LIMIT_AWARE,
        state_backend=state,
        config=RouterConfig(),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("audio-group", {})

    assert selected is not None
    assert selected.deployment_id == "dep-cool"


@pytest.mark.asyncio
async def test_pre_call_checks_use_router_state_usage():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-over",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"rpm_limit": 1, "tpm_limit": 10},
                },
                {
                    "deployment_id": "dep-ok",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"rpm_limit": 10, "tpm_limit": 100},
                },
            ]
        }
    )
    await state.increment_usage("dep-over", 20)
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state,
        config=RouterConfig(enable_pre_call_checks=True),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("gpt-4o-mini", {})

    assert selected is not None
    assert selected.deployment_id == "dep-ok"


@pytest.mark.asyncio
async def test_pre_call_checks_use_mode_specific_limits():
    state = RedisStateBackend(redis=None)
    registry = build_deployment_registry(
        {
            "rerank-group": [
                {
                    "deployment_id": "dep-over",
                    "deltallm_params": {"model": "openai/rerank"},
                    "model_info": {"mode": "rerank", "rpm_limit": 10, "rerank_units_pm_limit": 5},
                },
                {
                    "deployment_id": "dep-ok",
                    "deltallm_params": {"model": "openai/rerank"},
                    "model_info": {"mode": "rerank", "rpm_limit": 10, "rerank_units_pm_limit": 5},
                },
            ]
        }
    )
    await state.increment_usage_counters("dep-over", {"rpm": 1, "rerank_units_pm": 5})
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state,
        config=RouterConfig(enable_pre_call_checks=True),
        deployment_registry=registry,
    )

    selected = await router.select_deployment("rerank-group", {})

    assert selected is not None
    assert selected.deployment_id == "dep-ok"


def test_normalize_router_usage_keeps_non_token_modes_out_of_tpm():
    image_usage = normalize_router_usage(mode="image_generation", usage={"images": 2})
    assert image_usage == {"rpm": 1, "image_pm": 2}

    audio_usage = normalize_router_usage(
        mode="audio_transcription",
        usage={"duration_seconds": 1.2, "prompt_tokens": 99},
    )
    assert audio_usage == {"rpm": 1, "audio_seconds_pm": 2}

    rerank_usage = normalize_router_usage(
        mode="rerank", usage={"rerank_units": 4, "prompt_tokens": 120}
    )
    assert rerank_usage == {"rpm": 1, "rerank_units_pm": 4}


def test_normalize_router_usage_counts_multimodal_chat_tokens_without_total_tokens():
    usage = normalize_router_usage(
        mode="chat",
        usage={
            "prompt_tokens": 2,
            "completion_tokens": 3,
            "input_audio_tokens": 5,
            "output_audio_tokens": 7,
        },
    )
    assert usage == {"rpm": 1, "tpm": 17}

    fallback_usage = normalize_router_usage(
        mode="chat",
        usage={"prompt_tokens": 2, "completion_tokens": 3, "audio_tokens": 11},
    )
    assert fallback_usage == {"rpm": 1, "tpm": 16}


def test_build_deployment_registry_supports_explicit_route_groups():
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-a",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"weight": 1},
                },
                {
                    "deployment_id": "dep-b",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"weight": 1},
                },
            ]
        },
        route_groups=[
            {
                "key": "support-fast",
                "enabled": True,
                "members": [
                    {"deployment_id": "dep-b", "weight": 8},
                    {"deployment_id": "dep-a"},
                ],
            }
        ],
    )

    assert "gpt-4o-mini" in registry
    assert "support-fast" in registry
    assert [item.deployment_id for item in registry["support-fast"]] == ["dep-b", "dep-a"]
    assert registry["support-fast"][0].weight == 8


@pytest.mark.asyncio
async def test_group_policy_overrides_global_strategy():
    state = RedisStateBackend(redis=None)
    route_groups = [
        {
            "key": "support-route",
            "enabled": True,
            "strategy": "least-busy",
            "members": [
                {"deployment_id": "dep-a"},
                {"deployment_id": "dep-b"},
            ],
        }
    ]
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {
                    "deployment_id": "dep-a",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"input_cost_per_token": 0.1},
                },
                {
                    "deployment_id": "dep-b",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"input_cost_per_token": 0.9},
                },
            ]
        },
        route_groups=route_groups,
    )
    router = Router(
        strategy=RoutingStrategy.COST_BASED,
        state_backend=state,
        config=RouterConfig(route_group_policies=build_route_group_policies(route_groups)),
        deployment_registry=registry,
    )

    permits = [
        await state.acquire_attempt("dep-a", AttemptCapacity()),
        await state.acquire_attempt("dep-a", AttemptCapacity()),
    ]

    try:
        selected_in_group = await router.select_deployment("support-route", {})
        selected_legacy = await router.select_deployment("gpt-4o-mini", {})

        assert selected_in_group is not None
        assert selected_in_group.deployment_id == "dep-b"
        assert selected_legacy is not None
        assert selected_legacy.deployment_id == "dep-a"
    finally:
        for permit in permits:
            await state.release_attempt(permit)


@pytest.mark.asyncio
async def test_router_records_route_decision_envelope():
    state = RedisStateBackend(redis=None)
    route_groups = [
        {
            "key": "support-route",
            "enabled": True,
            "strategy": "least-busy",
            "policy_version": 7,
            "members": [{"deployment_id": "dep-a"}],
        }
    ]
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {"deployment_id": "dep-a", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
            ]
        },
        route_groups=route_groups,
    )
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state,
        config=RouterConfig(route_group_policies=build_route_group_policies(route_groups)),
        deployment_registry=registry,
    )
    request_context: dict[str, object] = {}

    selected = await router.select_deployment("support-route", request_context)

    assert selected is not None
    decision = request_context.get("route_decision")
    assert isinstance(decision, dict)
    assert decision["model_group"] == "support-route"
    assert decision["strategy"] == "least-busy"
    assert decision["policy_version"] == 7
    assert decision["selected_deployment_id"] == "dep-a"


@pytest.mark.asyncio
async def test_router_exposes_failover_overrides_from_policy():
    state = RedisStateBackend(redis=None)
    route_groups = [
        {
            "key": "support-route",
            "enabled": True,
            "strategy": "least-busy",
            "policy_version": 3,
            "timeouts": {"global_ms": 750},
            "retry": {"max_attempts": 2, "retryable_error_classes": ["timeout", "rate_limit"]},
            "members": [{"deployment_id": "dep-a"}],
        }
    ]
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {"deployment_id": "dep-a", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
            ]
        },
        route_groups=route_groups,
    )
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state,
        config=RouterConfig(route_group_policies=build_route_group_policies(route_groups)),
        deployment_registry=registry,
    )
    request_context: dict[str, object] = {}

    selected = await router.select_deployment("support-route", request_context)

    assert selected is not None
    policy = request_context.get("route_policy")
    assert isinstance(policy, dict)
    assert policy["timeout_seconds"] == 0.75
    assert policy["retry_max_attempts"] == 2
    assert policy["retryable_error_classes"] == ["rate_limit", "timeout"]


@pytest.mark.asyncio
async def test_router_state_fail_open_uses_bounded_local_fallback():
    state = RedisStateBackend(redis=None, degraded_mode="fail_open", max_local_latency_samples=2)

    permit = await state.acquire_attempt("dep-a", AttemptCapacity())
    await state.record_latency("dep-a", 10.0)
    await state.record_latency("dep-a", 20.0)
    await state.record_latency("dep-a", 30.0)

    assert await state.get_active_requests("dep-a") == 1
    latency_window = await state.get_latency_window("dep-a", 300_000)
    assert [lat for _, lat in latency_window] == [20.0, 30.0]
    assert state.get_backend_status()["mode"] == "degraded"
    assert await state.release_attempt(permit) == 0


@pytest.mark.asyncio
async def test_router_state_fail_closed_raises_when_backend_unavailable():
    state = RedisStateBackend(redis=None, degraded_mode="fail_closed")

    with pytest.raises(ServiceUnavailableError, match="Router state backend unavailable"):
        await state.get_active_requests("dep-a")

    assert state.get_backend_status()["mode"] == "unavailable"


@pytest.mark.asyncio
async def test_health_transition_fails_closed_when_backend_is_unavailable():
    state = RedisStateBackend(redis=None, degraded_mode="fail_closed")
    cooldown = CooldownManager(state, allowed_fails=0)

    with pytest.raises(ServiceUnavailableError, match="Router state backend unavailable"):
        await cooldown.record_failure("dep-a", "provider unavailable")

    assert state.get_backend_status()["mode"] == "unavailable"


@pytest.mark.asyncio
async def test_router_state_drops_zero_active_local_entries():
    state = RedisStateBackend(redis=None, degraded_mode="fail_open")

    permit = await state.acquire_attempt("dep-a", AttemptCapacity())
    value = await state.release_attempt(permit)

    assert value == 0
    assert await state.get_active_requests("dep-a") == 0
    assert "dep-a" not in state._active
    assert "dep-a" not in state._local_last_seen


@pytest.mark.asyncio
async def test_router_state_prunes_stale_local_entries(monkeypatch: pytest.MonkeyPatch):
    state = RedisStateBackend(redis=None, degraded_mode="fail_open", local_state_ttl_sec=1)
    now = {"value": 1_000.0}

    monkeypatch.setattr("src.router.state.time.time", lambda: now["value"])
    await CooldownManager(state, allowed_fails=10).record_failure("dep-a", "boom")

    now["value"] = 1_005.0
    health = await state.get_health("dep-a")

    assert health == {}
    assert "dep-a" not in state._local_last_seen


@pytest.mark.asyncio
async def test_health_handler_surfaces_degraded_router_state():
    registry = build_deployment_registry(
        {
            "gpt-4o-mini": [
                {"deployment_id": "dep-a", "deltallm_params": {"model": "openai/gpt-4o-mini"}},
            ]
        }
    )
    state = RedisStateBackend(redis=None, degraded_mode="fail_open")
    handler = HealthEndpointHandler(deployment_registry=registry, state_backend=state)

    payload = await handler.get_health_status()

    assert payload["status"] == "degraded"
    assert payload["state_backend"]["mode"] == "degraded"
