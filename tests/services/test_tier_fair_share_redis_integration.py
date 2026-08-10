from __future__ import annotations

import os
from time import perf_counter
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from src.models.errors import RateLimitError
from src.services.limit_counter import LimitCounter
from src.services.tier_capacity_fair_share import (
    TierFairShareCheck,
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
    pool_key = f"integration-{unique}"
    callable_key = "model"
    org_a = f"org-a-{unique}"
    org_b = f"org-b-{unique}"
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_closed")

    def check(organization_id: str, weight: int, amount: int = 1) -> TierFairShareCheck:
        return TierFairShareCheck(
            pool_key=pool_key,
            callable_key=callable_key,
            organization_id=organization_id,
            tier_key="integration",
            assignment_weight=weight,
            rpm_capacity=10,
            tpm_capacity=None,
            request_amount=amount,
            token_amount=0,
            strategy="weighted_fair",
            saturation_threshold=0.5,
            burst_multiplier=None,
        )

    try:
        await limiter.check_rate_limits_and_tier_fair_share_atomic([], [check(org_a, 1)])
        await limiter.check_rate_limits_and_tier_fair_share_atomic([], [check(org_b, 3)])

        # A may borrow otherwise-idle capacity while the pool is below saturation.
        borrowed = await limiter.check_rate_limits_and_tier_fair_share_atomic(
            [],
            [check(org_a, 1, amount=2)],
        )
        assert borrowed.fair_share_decisions[0].saturation == pytest.approx(0.4)

        saturated = await limiter.check_rate_limits_and_tier_fair_share_atomic(
            [],
            [check(org_b, 3)],
        )
        decision = saturated.fair_share_decisions[0]
        assert decision.saturation == pytest.approx(0.5)
        assert decision.active_org_count == 2
        assert decision.share_limit == 7

        with pytest.raises(RateLimitError) as exc_info:
            await limiter.check_rate_limits_and_tier_fair_share_atomic([], [check(org_a, 1)])
        assert exc_info.value.param == "tier_pool_fair_share_rpm"

        # A temporary boost is consumed by the same atomic Lua path and expires in Redis.
        boost_key = fair_share_boost_key(
            pool_key=pool_key,
            callable_key=callable_key,
            organization_id=org_a,
        )
        await redis.set(boost_key, "4", ex=30)
        boosted = await limiter.check_rate_limits_and_tier_fair_share_atomic(
            [],
            [check(org_a, 1)],
        )
        boosted_decision = boosted.fair_share_decisions[0]
        assert boosted_decision.effective_weight == 4
        assert boosted_decision.share_limit == 5
        assert 0 < await redis.ttl(boost_key) <= 30

        benchmark_check = TierFairShareCheck(
            pool_key=f"benchmark-{unique}",
            callable_key=callable_key,
            organization_id=org_a,
            tier_key="integration",
            assignment_weight=1,
            rpm_capacity=1_000_000,
            tpm_capacity=None,
            request_amount=1,
            token_amount=0,
            strategy="weighted_fair",
            saturation_threshold=1,
            burst_multiplier=None,
        )
        started = perf_counter()
        for _ in range(1_000):
            await limiter.check_rate_limits_and_tier_fair_share_atomic([], [benchmark_check])
        assert perf_counter() - started < 5
    finally:
        keys = [key async for key in redis.scan_iter(match=f"*{unique}*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
