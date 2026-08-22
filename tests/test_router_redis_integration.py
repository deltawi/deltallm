from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from src.chat.stream_response import DeadlineStreamingResponse
from src.router import (
    AttemptCapacity,
    AttemptCapacityLimit,
    AttemptRejectionReason,
    CooldownManager,
    RedisStateBackend,
    RouterRedisKeyspace,
    Router,
    RouterConfig,
    RoutingStrategy,
)
from src.router.candidates import ROUTING_MODE_CONTEXT_KEY
from src.router.execution import ManagedFailoverResult, RequestDeadline
from src.router.health_state import DeploymentHealthRef, HEALTH_STATE_RETENTION_SECONDS
from src.router.router import Deployment


@pytest.mark.skipif(
    not os.getenv("DELTALLM_TEST_REDIS_URL"),
    reason="DELTALLM_TEST_REDIS_URL is required for the Redis integration test",
)
async def test_candidate_eligibility_uses_real_redis_batch_state_under_concurrency() -> None:
    redis = Redis.from_url(
        os.environ["DELTALLM_TEST_REDIS_URL"],
        decode_responses=True,
    )
    unique = uuid4().hex

    def deployment(
        suffix: str,
        *,
        mode: str = "chat",
        provider: str = "openai",
        tags: list[str] | None = None,
        rpm_limit: int | None = None,
    ) -> Deployment:
        return Deployment(
            deployment_id=f"router-integration-{suffix}-{unique}",
            model_name="integration-group",
            deltallm_params={
                "provider": provider,
                "model": f"{provider}/integration-model",
            },
            model_info={"mode": mode},
            tags=list(tags or []),
            rpm_limit=rpm_limit,
        )

    unhealthy = deployment("unhealthy", tags=["vip"])
    cooled = deployment("cooled", tags=["vip"])
    at_capacity = deployment("at-capacity", tags=["vip"], rpm_limit=1)
    wrong_tag = deployment("wrong-tag", tags=["standard"])
    wrong_mode = deployment("wrong-mode", mode="embedding", tags=["vip"])
    unsupported = deployment("unsupported", provider="elevenlabs", tags=["vip"])
    eligible = deployment("eligible", tags=["vip"])
    deployments = [
        unhealthy,
        cooled,
        at_capacity,
        wrong_tag,
        wrong_mode,
        unsupported,
        eligible,
    ]
    state = RedisStateBackend(redis, degraded_mode="fail_closed")
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state,
        config=RouterConfig(enable_pre_call_checks=True),
        deployment_registry={"integration-group": deployments},
    )

    try:
        await redis.hset(
            state.keyspace.health(unhealthy.deployment_id),
            mapping={"healthy": "false"},
        )
        await CooldownManager(state).manual_cooldown(
            cooled.deployment_id,
            60,
            "integration",
        )
        await state.increment_usage(at_capacity.deployment_id, 0)
        await state.record_latency(eligible.deployment_id, 12.5)

        deployment_ids = [deployment.deployment_id for deployment in deployments]
        usage, health, cooldowns, latency = await asyncio.gather(
            state.get_usage_batch(deployment_ids),
            state.get_health_batch(deployment_ids),
            state.get_cooldown_batch(deployment_ids),
            state.get_latency_windows_batch(deployment_ids, 300_000),
        )

        assert usage[at_capacity.deployment_id]["rpm"] == 1
        assert health[unhealthy.deployment_id]["healthy"] == "false"
        assert cooldowns[cooled.deployment_id] is True
        assert len(latency[eligible.deployment_id]) == 1

        contexts = [
            {
                ROUTING_MODE_CONTEXT_KEY: "chat",
                "metadata": {"tags": ["vip"]},
            }
            for _ in range(16)
        ]
        plans = await asyncio.gather(
            *(router.plan_deployments(["integration-group"], context) for context in contexts)
        )

        assert [
            [deployment.deployment_id for deployment in plan["integration-group"].deployments]
            for plan in plans
        ] == [[eligible.deployment_id] for _ in contexts]
        assert state.get_backend_status()["mode"] == "redis"
    finally:
        keys = [key async for key in redis.scan_iter(match=f"*{unique}*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


@pytest.mark.skipif(
    not os.getenv("DELTALLM_TEST_REDIS_URL"),
    reason="DELTALLM_TEST_REDIS_URL is required for the Redis integration test",
)
async def test_stream_disconnect_releases_managed_attempt_in_real_redis() -> None:
    redis = Redis.from_url(
        os.environ["DELTALLM_TEST_REDIS_URL"],
        decode_responses=True,
    )
    unique = uuid4().hex
    deployment = Deployment(
        deployment_id=f"router-managed-stream-{unique}",
        model_name="integration-group",
        deltallm_params={"provider": "openai", "model": "openai/integration-model"},
        model_info={"mode": "chat"},
    )
    state = RedisStateBackend(redis, degraded_mode="fail_closed")

    try:
        permit = await state.acquire_attempt(deployment.deployment_id, AttemptCapacity())
        assert permit.acquired is True

        async def release() -> None:
            await state.release_attempt(permit)

        managed = ManagedFailoverResult(
            value="opened-stream",
            deployment=deployment,
            deadline=RequestDeadline.after(10),
            _release=release,
        )

        async def body():
            yield "data: chunk\n\n"

        async def send(message):  # noqa: ANN001, ANN202
            if message["type"] != "http.response.body" or not message.get("more_body"):
                return
            assert (
                int(await redis.get(state.keyspace.active_requests(deployment.deployment_id)) or 0)
                == 1
            )
            raise OSError("client disconnected")

        response = DeadlineStreamingResponse(
            body(),
            deadline=managed.deadline,
            close=lambda _exc: managed.release(),
            media_type="text/event-stream",
        )
        with pytest.raises(OSError, match="client disconnected"):
            await response.stream_response(send)

        assert (
            int(await redis.get(state.keyspace.active_requests(deployment.deployment_id)) or 0) == 0
        )
    finally:
        keys = [key async for key in redis.scan_iter(match=f"*{unique}*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


@pytest.mark.skipif(
    not os.getenv("DELTALLM_TEST_REDIS_URL"),
    reason="DELTALLM_TEST_REDIS_URL is required for the Redis integration test",
)
async def test_attempt_admission_is_atomic_and_recovers_with_real_redis() -> None:
    redis = Redis.from_url(
        os.environ["DELTALLM_TEST_REDIS_URL"],
        decode_responses=True,
    )
    unique = uuid4().hex

    class ToggleEvalOutageRedis:
        def __init__(self, delegate: Redis) -> None:
            self.delegate = delegate
            self.fail_eval = False
            self.fail_release = False

        async def eval(self, script: str, numkeys: int, *args):  # noqa: ANN002, ANN201
            if self.fail_eval or (self.fail_release and "router_attempt_release_v2" in script):
                raise RuntimeError("simulated redis outage")
            return await self.delegate.eval(script, numkeys, *args)

        def __getattr__(self, name: str):  # noqa: ANN204
            return getattr(self.delegate, name)

    wrapper = ToggleEvalOutageRedis(redis)
    state = RedisStateBackend(wrapper, degraded_mode="fail_open")
    cooled_id = f"router-attempt-cooled-{unique}"
    capacity_id = f"router-attempt-capacity-{unique}"
    active_id = f"router-attempt-active-{unique}"
    recovery_id = f"router-attempt-recovery-{unique}"
    release_outage_id = f"router-attempt-release-outage-{unique}"

    try:
        await CooldownManager(state).manual_cooldown(cooled_id, 60, "integration")
        cooled_permits = await asyncio.gather(
            *(state.acquire_attempt(cooled_id, AttemptCapacity()) for _ in range(16))
        )

        assert all(not permit.acquired for permit in cooled_permits)
        assert {permit.rejection_reason for permit in cooled_permits} == {
            AttemptRejectionReason.COOLDOWN
        }
        assert await redis.get(state.keyspace.active_requests(cooled_id)) is None
        assert 0 < await redis.ttl(state.keyspace.cooldown(cooled_id)) <= 60

        await state.increment_usage(capacity_id, 0)
        capacity_permit = await state.acquire_attempt(
            capacity_id,
            AttemptCapacity((AttemptCapacityLimit("rpm", 1),)),
        )

        assert capacity_permit.acquired is False
        assert capacity_permit.rejection_reason == AttemptRejectionReason.CAPACITY
        assert await redis.get(state.keyspace.active_requests(capacity_id)) is None

        permits = await asyncio.gather(
            *(state.acquire_attempt(active_id, AttemptCapacity()) for _ in range(16))
        )

        assert all(permit.acquired for permit in permits)
        assert len({permit.owner_token for permit in permits}) == len(permits)
        assert sorted(permit.active_requests for permit in permits) == list(range(1, 17))
        assert int(await redis.get(state.keyspace.active_requests(active_id)) or 0) == 16
        assert 0 < await redis.ttl(state.keyspace.active_requests(active_id)) <= 631
        assert 0 < await redis.ttl(state._attempt_owners_key(active_id)) <= 631

        await asyncio.gather(*(state.release_attempt(permit) for permit in permits))

        assert int(await redis.get(state.keyspace.active_requests(active_id)) or 0) == 0

        wrapper.fail_eval = True
        local_permit = await state.acquire_attempt(recovery_id, AttemptCapacity())

        assert local_permit.acquired is True
        assert local_permit.backend == "local"
        assert state.get_backend_status()["mode"] == "degraded"

        wrapper.fail_eval = False
        assert await state.release_attempt(local_permit) == 0
        recovered_permit = await state.acquire_attempt(recovery_id, AttemptCapacity())

        assert recovered_permit.acquired is True
        assert recovered_permit.backend == "redis"
        assert state.get_backend_status()["mode"] == "redis"
        assert await state.release_attempt(recovered_permit) == 0

        release_outage_permit = await state.acquire_attempt(
            release_outage_id,
            AttemptCapacity(),
            lease_ttl_seconds=1,
        )
        wrapper.fail_release = True

        assert await state.release_attempt(release_outage_permit) is None
        assert int(await redis.get(state.keyspace.active_requests(release_outage_id)) or 0) == 1
        assert 0 < await redis.ttl(state.keyspace.active_requests(release_outage_id)) <= 2
        assert 0 < await redis.ttl(state._attempt_owners_key(release_outage_id)) <= 2

        wrapper.fail_release = False
        deadline = asyncio.get_running_loop().time() + 5
        while await redis.exists(state.keyspace.active_requests(release_outage_id)):
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail("attempt permit keys did not expire after release outage")
            await asyncio.sleep(0.05)

        recovered_after_release_outage = await state.acquire_attempt(
            release_outage_id,
            AttemptCapacity(),
        )
        assert recovered_after_release_outage.active_requests == 1
        assert state.get_backend_status()["mode"] == "redis"
        assert await state.release_attempt(recovered_after_release_outage) == 0
    finally:
        keys = [key async for key in redis.scan_iter(match=f"*{unique}*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


@pytest.mark.skipif(
    not os.getenv("DELTALLM_TEST_REDIS_URL"),
    reason="DELTALLM_TEST_REDIS_URL is required for the Redis integration test",
)
async def test_health_cooldown_transition_and_half_open_claim_are_atomic_in_real_redis() -> None:
    redis = Redis.from_url(
        os.environ["DELTALLM_TEST_REDIS_URL"],
        decode_responses=True,
    )
    unique = uuid4().hex
    recovery_id = f"router-health-recovery-{unique}"
    concurrent_id = f"router-health-concurrent-{unique}"
    failed_recovery_id = f"router-health-failed-recovery-{unique}"
    probe_claim_id = f"router-health-probe-claim-{unique}"
    manual_probe_claim_id = f"router-health-manual-probe-claim-{unique}"
    invalidation_id = f"router-health-invalidation-{unique}"
    invalidation_control_id = f"router-health-invalidation-control-{unique}"
    state = RedisStateBackend(redis, degraded_mode="fail_closed")
    cooldown = CooldownManager(state, cooldown_time=1, allowed_fails=0)
    concurrent_cooldown = CooldownManager(state, cooldown_time=60, allowed_fails=0)

    try:
        assert await cooldown.record_failure(recovery_id, "provider unavailable") is True
        assert await redis.hget(state.keyspace.health(recovery_id), "healthy") == "false"
        assert (
            0
            < await redis.ttl(state.keyspace.health(recovery_id))
            <= (HEALTH_STATE_RETENTION_SECONDS)
        )
        await cooldown.record_success(recovery_id)
        await cooldown.record_failure(recovery_id, "stale in-flight failure")
        assert await redis.hget(state.keyspace.health(recovery_id), "healthy") == "false"
        assert await redis.hget(state.keyspace.health(recovery_id), "consecutive_failures") == "1"

        deadline = asyncio.get_running_loop().time() + 5
        while await redis.exists(state.keyspace.cooldown(recovery_id)):
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail("cooldown key did not expire")
            await asyncio.sleep(0.05)

        await cooldown.record_success(recovery_id)
        assert await redis.hget(state.keyspace.health(recovery_id), "healthy") == "false"

        permits = await asyncio.gather(
            *(state.acquire_attempt(recovery_id, AttemptCapacity()) for _ in range(16))
        )
        acquired = [permit for permit in permits if permit.acquired]
        rejected = [permit for permit in permits if not permit.acquired]
        assert len(acquired) == 1
        assert acquired[0].recovery is True
        assert {permit.rejection_reason for permit in rejected} == {
            AttemptRejectionReason.RECOVERY_IN_PROGRESS
        }
        assert await state.claim_health_probe(recovery_id, 60) is None

        await cooldown.record_success(
            recovery_id,
            recovery_token=acquired[0].owner_token,
        )
        await state.release_attempt(acquired[0])
        health = await state.get_health(recovery_id)
        assert health["healthy"] == "true"
        assert health["consecutive_failures"] == "0"
        assert (
            0
            < await redis.ttl(state.keyspace.health(recovery_id))
            <= (HEALTH_STATE_RETENTION_SECONDS)
        )

        transitions = await asyncio.gather(
            *(
                concurrent_cooldown.record_failure(concurrent_id, "provider unavailable")
                for _ in range(2)
            )
        )
        assert transitions.count(True) == 1
        assert transitions.count(False) == 1
        concurrent_health = await state.get_health(concurrent_id)
        assert concurrent_health["consecutive_failures"] == "1"
        assert 0 < await redis.ttl(state.keyspace.cooldown(concurrent_id)) <= 60

        delayed_recovery = CooldownManager(state, cooldown_time=1, allowed_fails=2)
        await delayed_recovery.manual_cooldown(failed_recovery_id, 1, "operator cooldown")
        await delayed_recovery.record_success(failed_recovery_id)
        assert await redis.exists(state.keyspace.cooldown(failed_recovery_id)) == 1
        assert await redis.hget(state.keyspace.health(failed_recovery_id), "healthy") == "false"
        deadline = asyncio.get_running_loop().time() + 5
        while await redis.exists(state.keyspace.cooldown(failed_recovery_id)):
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail("manual cooldown key did not expire")
            await asyncio.sleep(0.05)
        probe_recovery_claim = await state.claim_health_probe(failed_recovery_id, 60)
        assert probe_recovery_claim is not None
        assert probe_recovery_claim.recovery is True
        await state.release_health_recovery(failed_recovery_id, "not-the-owner")
        blocked_by_probe = await state.acquire_attempt(failed_recovery_id, AttemptCapacity())
        assert blocked_by_probe.acquired is False
        assert blocked_by_probe.rejection_reason == AttemptRejectionReason.RECOVERY_IN_PROGRESS
        await state.release_health_recovery(
            failed_recovery_id,
            probe_recovery_claim.owner_token,
        )
        recovery_permit = await state.acquire_attempt(failed_recovery_id, AttemptCapacity())
        assert recovery_permit.acquired is True
        assert recovery_permit.recovery is True
        assert (
            await delayed_recovery.record_failure(
                failed_recovery_id,
                "half-open provider failure",
                recovery_token=recovery_permit.owner_token,
            )
            is True
        )
        failed_recovery_health = await state.get_health(failed_recovery_id)
        assert failed_recovery_health["consecutive_failures"] == "1"
        assert 0 < await redis.ttl(state.keyspace.cooldown(failed_recovery_id)) <= 1

        first_replica = RedisStateBackend(redis, degraded_mode="fail_closed")
        second_replica = RedisStateBackend(redis, degraded_mode="fail_closed")
        claims = await asyncio.gather(
            *(
                replica.claim_health_probe(probe_claim_id, 60)
                for replica in (first_replica, second_replica)
                for _ in range(8)
            )
        )
        assert sum(claim is not None for claim in claims) == 1
        assert sum(claim is None for claim in claims) == 15

        manual_claim = await state.claim_health_probe(
            manual_probe_claim_id,
            60,
            scope="manual",
        )
        assert manual_claim is not None
        assert (
            await state.claim_health_probe(
                manual_probe_claim_id,
                60,
                scope="manual",
            )
            is None
        )
        await state.release_health_probe(manual_probe_claim_id, manual_claim)
        replacement_manual_claim = await state.claim_health_probe(
            manual_probe_claim_id,
            60,
            scope="manual",
        )
        assert replacement_manual_claim is not None
        await state.release_health_probe(manual_probe_claim_id, replacement_manual_claim)

        isolated_environment = RedisStateBackend(
            redis,
            degraded_mode="fail_closed",
            keyspace=RouterRedisKeyspace(environment="isolated-integration"),
        )
        isolated_claim = await isolated_environment.claim_health_probe(probe_claim_id, 60)
        assert isolated_claim is not None
        assert (
            await redis.exists(
                isolated_environment.keyspace.health_probe(probe_claim_id, "background")
            )
            == 1
        )
        environment_isolation_id = f"router-health-environment-{unique}"
        await concurrent_cooldown.record_failure(
            environment_isolation_id,
            "default environment failure",
        )
        assert (await state.get_health(environment_isolation_id))["healthy"] == "false"
        assert await isolated_environment.get_health(environment_isolation_id) == {}
        isolated_permit = await isolated_environment.acquire_attempt(
            environment_isolation_id,
            AttemptCapacity(),
        )
        assert isolated_permit.acquired is True
        assert isolated_permit.recovery is False
        await isolated_environment.release_attempt(isolated_permit)

        await concurrent_cooldown.record_failure(invalidation_id, "provider unavailable")
        await concurrent_cooldown.record_failure(
            invalidation_control_id,
            "provider unavailable",
        )
        assert await state.claim_health_probe(invalidation_id, 60) is not None
        assert await state.claim_health_probe(invalidation_id, 60, scope="manual") is not None
        await redis.set(state.keyspace.health_recovery(invalidation_id), "stale-owner", ex=60)
        await redis.set(state.keyspace.active_requests(invalidation_id), "3", ex=60)

        assert await state.invalidate_health_state([invalidation_id]) is True

        invalidated_keys = (
            state.keyspace.health_failures(invalidation_id),
            state.keyspace.health(invalidation_id),
            state.keyspace.cooldown(invalidation_id),
            state.keyspace.health_recovery(invalidation_id),
            state.keyspace.health_probe(invalidation_id, "background"),
            state.keyspace.health_probe(invalidation_id, "manual"),
        )
        assert await asyncio.gather(*(redis.exists(key) for key in invalidated_keys)) == [0] * len(
            invalidated_keys
        )
        assert await redis.exists(state.keyspace.active_requests(invalidation_id)) == 1
        assert await redis.exists(state.keyspace.health(invalidation_control_id)) == 1
        assert await redis.exists(state.keyspace.cooldown(invalidation_control_id)) == 1
    finally:
        keys = [key async for key in redis.scan_iter(match=f"*{unique}*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


@pytest.mark.skipif(
    not os.getenv("DELTALLM_TEST_REDIS_URL"),
    reason="DELTALLM_TEST_REDIS_URL is required for the Redis integration test",
)
async def test_health_state_reports_local_degradation_and_discards_it_after_redis_reconnect() -> (
    None
):
    redis = Redis.from_url(
        os.environ["DELTALLM_TEST_REDIS_URL"],
        decode_responses=True,
    )
    unique = uuid4().hex
    deployment_id = f"router-health-outage-{unique}"

    class ToggleHealthOutageRedis:
        def __init__(self, delegate: Redis) -> None:
            self.delegate = delegate
            self.fail_eval = False

        async def eval(self, script: str, numkeys: int, *args):  # noqa: ANN002, ANN201
            if self.fail_eval:
                raise RuntimeError("simulated health state outage")
            return await self.delegate.eval(script, numkeys, *args)

        def __getattr__(self, name: str):  # noqa: ANN204
            return getattr(self.delegate, name)

    wrapper = ToggleHealthOutageRedis(redis)
    state = RedisStateBackend(wrapper, degraded_mode="fail_open")
    cooldown = CooldownManager(state, cooldown_time=60, allowed_fails=0)

    try:
        wrapper.fail_eval = True
        assert await cooldown.record_failure(deployment_id, "provider unavailable") is True
        assert state.get_backend_status()["mode"] == "degraded"
        legacy_ref = DeploymentHealthRef(deployment_id)
        assert state._health[legacy_ref]["healthy"] == "false"

        wrapper.fail_eval = False
        assert await state.get_health(deployment_id) == {}
        assert state.get_backend_status()["mode"] == "redis"
        assert legacy_ref not in state._health
        assert legacy_ref not in state._cooldown_until
    finally:
        keys = [key async for key in redis.scan_iter(match=f"*{unique}*")]
        if keys:
            await redis.delete(*keys)
    await redis.aclose()


@pytest.mark.skipif(
    not os.getenv("DELTALLM_TEST_REDIS_URL"),
    reason="DELTALLM_TEST_REDIS_URL is required for the Redis integration test",
)
async def test_retired_health_generation_isolated_from_replacement_across_replicas() -> None:
    old_redis = Redis.from_url(
        os.environ["DELTALLM_TEST_REDIS_URL"],
        decode_responses=True,
    )
    replacement_redis = Redis.from_url(
        os.environ["DELTALLM_TEST_REDIS_URL"],
        decode_responses=True,
    )
    unique = uuid4().hex
    deployment_id = f"router-generation-{unique}"
    old_ref = DeploymentHealthRef(deployment_id, "old-generation")
    replacement_ref = DeploymentHealthRef(deployment_id, "replacement-generation")
    old_replica = RedisStateBackend(old_redis, degraded_mode="fail_closed")
    replacement_replica = RedisStateBackend(replacement_redis, degraded_mode="fail_closed")
    old_health = CooldownManager(old_replica, cooldown_time=60, allowed_fails=0)

    try:
        old_permit = await old_replica.acquire_attempt(old_ref, AttemptCapacity())
        assert old_permit.acquired is True

        assert await replacement_replica.invalidate_health_state([old_ref]) is True
        await old_health.record_failure(old_ref, "late old-generation failure")

        assert (await old_replica.get_health(old_ref))["healthy"] == "false"
        assert await replacement_replica.get_health(replacement_ref) == {}
        assert await replacement_replica.is_cooled_down(replacement_ref) is False

        replacement_permit = await replacement_replica.acquire_attempt(
            replacement_ref,
            AttemptCapacity(),
        )
        assert replacement_permit.acquired is True
        assert replacement_permit.recovery is False
        assert (
            0
            < await old_redis.ttl(old_replica.keyspace.health(deployment_id, old_ref.generation))
            <= HEALTH_STATE_RETENTION_SECONDS
        )

        await replacement_replica.release_attempt(replacement_permit)
        await old_replica.release_attempt(old_permit)
    finally:
        keys = [key async for key in old_redis.scan_iter(match=f"*{unique}*")]
        if keys:
            await old_redis.delete(*keys)
        await old_redis.aclose()
        await replacement_redis.aclose()
