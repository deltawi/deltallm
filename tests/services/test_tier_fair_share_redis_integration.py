from __future__ import annotations

import os
from time import perf_counter
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from src.models.errors import RateLimitError
from src.services.limit_counter import (
    FairShareLimit,
    LimitCounter,
    RateLimitCheck,
    fair_share_boost_key,
)


@pytest.mark.skipif(
    not os.getenv("DELTALLM_TEST_REDIS_URL"),
    reason="DELTALLM_TEST_REDIS_URL is required for the Redis integration test",
)
async def test_weighted_fair_share_lua_against_real_redis() -> None:
    redis = Redis.from_url(
        os.environ["DELTALLM_TEST_REDIS_URL"],
        decode_responses=True,
    )
    unique = uuid4().hex
    entity_id = f"integration-{unique}:model"
    org_a = f"org-a-{unique}"
    org_b = f"org-b-{unique}"
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_closed")

    def check(organization_id: str, weight: int, amount: int = 1) -> RateLimitCheck:
        return RateLimitCheck(
            scope="tier_pool_model_rpm",
            entity_id=entity_id,
            limit=10,
            amount=amount,
            fair_share=FairShareLimit(
                organization_id=organization_id,
                weight=weight,
                saturation_threshold=0.5,
            ),
        )

    try:
        await limiter.check_rate_limits_atomic([check(org_a, 1)])
        await limiter.check_rate_limits_atomic([check(org_b, 3)])

        # A may borrow otherwise-idle capacity while the pool is below saturation.
        borrowed = await limiter.check_rate_limits_atomic([check(org_a, 1, amount=2)])
        assert borrowed.fair_share_observations[0].saturated is False

        saturated = await limiter.check_rate_limits_atomic([check(org_b, 3)])
        observation = saturated.fair_share_observations[0]
        assert observation.saturated is True
        assert observation.active_organizations == 2
        assert observation.share_limit == 7

        with pytest.raises(RateLimitError) as exc_info:
            await limiter.check_rate_limits_atomic([check(org_a, 1)])
        assert exc_info.value.param == "tier_pool_model_rpm_fair_share"

        # A temporary boost is consumed by the same atomic Lua path and expires in Redis.
        boost_key = fair_share_boost_key(entity_id, org_a)
        await redis.set(boost_key, "4", ex=30)
        boosted = await limiter.check_rate_limits_atomic([check(org_a, 1)])
        boosted_observation = boosted.fair_share_observations[0]
        assert boosted_observation.capacity_boost_multiplier == 4
        assert boosted_observation.share_limit == 5
        assert 0 < await redis.ttl(boost_key) <= 30

        benchmark_check = RateLimitCheck(
            scope="tier_pool_model_rpm",
            entity_id=f"benchmark-{unique}:model",
            limit=1_000_000,
            fair_share=FairShareLimit(
                organization_id=org_a,
                weight=1,
                saturation_threshold=1,
            ),
        )
        started = perf_counter()
        for _ in range(1_000):
            await limiter.check_rate_limits_atomic([benchmark_check])
        assert perf_counter() - started < 5
    finally:
        keys = [key async for key in redis.scan_iter(match=f"*{unique}*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
