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
    RedisStateBackend,
    Router,
    RouterConfig,
    RoutingStrategy,
)
from src.router.candidates import ROUTING_MODE_CONTEXT_KEY
from src.router.execution import ManagedFailoverResult, RequestDeadline
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
        await state.set_health(unhealthy.deployment_id, False)
        await state.set_cooldown(cooled.deployment_id, 60, "integration")
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
            assert int(await redis.get(f"active_requests:{deployment.deployment_id}") or 0) == 1
            raise OSError("client disconnected")

        response = DeadlineStreamingResponse(
            body(),
            deadline=managed.deadline,
            close=lambda _exc: managed.release(),
            media_type="text/event-stream",
        )
        with pytest.raises(OSError, match="client disconnected"):
            await response.stream_response(send)

        assert int(await redis.get(f"active_requests:{deployment.deployment_id}") or 0) == 0
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
        await state.set_cooldown(cooled_id, 60, "integration")
        cooled_permits = await asyncio.gather(
            *(state.acquire_attempt(cooled_id, AttemptCapacity()) for _ in range(16))
        )

        assert all(not permit.acquired for permit in cooled_permits)
        assert {permit.rejection_reason for permit in cooled_permits} == {
            AttemptRejectionReason.COOLDOWN
        }
        assert await redis.get(f"active_requests:{cooled_id}") is None
        assert 0 < await redis.ttl(f"cooldown:{cooled_id}") <= 60

        await state.increment_usage(capacity_id, 0)
        capacity_permit = await state.acquire_attempt(
            capacity_id,
            AttemptCapacity((AttemptCapacityLimit("rpm", 1),)),
        )

        assert capacity_permit.acquired is False
        assert capacity_permit.rejection_reason == AttemptRejectionReason.CAPACITY
        assert await redis.get(f"active_requests:{capacity_id}") is None

        permits = await asyncio.gather(
            *(state.acquire_attempt(active_id, AttemptCapacity()) for _ in range(16))
        )

        assert all(permit.acquired for permit in permits)
        assert len({permit.owner_token for permit in permits}) == len(permits)
        assert sorted(permit.active_requests for permit in permits) == list(range(1, 17))
        assert int(await redis.get(f"active_requests:{active_id}") or 0) == 16
        assert 0 < await redis.ttl(f"active_requests:{active_id}") <= 631
        assert 0 < await redis.ttl(state._attempt_owners_key(active_id)) <= 631

        await asyncio.gather(*(state.release_attempt(permit) for permit in permits))

        assert int(await redis.get(f"active_requests:{active_id}") or 0) == 0

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
        assert int(await redis.get(f"active_requests:{release_outage_id}") or 0) == 1
        assert 0 < await redis.ttl(f"active_requests:{release_outage_id}") <= 2
        assert 0 < await redis.ttl(state._attempt_owners_key(release_outage_id)) <= 2

        wrapper.fail_release = False
        deadline = asyncio.get_running_loop().time() + 5
        while await redis.exists(f"active_requests:{release_outage_id}"):
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
