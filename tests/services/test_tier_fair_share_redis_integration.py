from __future__ import annotations

import os
from time import perf_counter
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from src.models.errors import RateLimitError
from src.services.limit_counter import LimitCounter, RateLimitCheck
from src.services.tier_capacity_fair_share import (
    TierFairShareCheck,
    fair_share_boost_key,
    fair_share_limit_hit_heatmap_key,
    fair_share_limit_hit_heatmap_rank_key,
    fair_share_limit_hit_total_key,
)
from src.tier_rate_limit_policy import TierCapacityRateCheck


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


@pytest.mark.skipif(
    not os.getenv("DELTALLM_TEST_REDIS_URL"),
    reason="DELTALLM_TEST_REDIS_URL is required for the Redis integration test",
)
async def test_static_hard_cap_telemetry_lua_against_real_redis() -> None:
    redis = Redis.from_url(
        os.environ["DELTALLM_TEST_REDIS_URL"],
        decode_responses=True,
    )
    unique = uuid4().hex
    pool_key = f"hard-cap-{unique}"
    callable_key = "model"
    organization_id = f"org-{unique}"
    limiter = LimitCounter(redis_client=redis, degraded_mode="fail_closed")
    rate_check = RateLimitCheck(
        scope="tier_pool_model_rpm",
        entity_id=f"{pool_key}:{callable_key}",
        limit=1,
        amount=1,
        window_seconds=60,
    )
    capacity_check = TierCapacityRateCheck(
        rate_check=rate_check,
        pool_key=pool_key,
        callable_key=callable_key,
        organization_id=organization_id,
        tier_key="integration",
        dimension="rpm",
    )
    heatmap_key = fair_share_limit_hit_heatmap_key()
    rank_key = fair_share_limit_hit_heatmap_rank_key()
    total_key = fair_share_limit_hit_total_key()
    field = (
        f"{pool_key}|{callable_key}|{organization_id}|"
        "tier_pool_model_rpm|integration"
    )

    try:
        admitted = await limiter.check_rate_limits_and_tier_fair_share_atomic(
            [rate_check],
            [],
            capacity_rate_checks=[capacity_check],
        )
        assert admitted.rate_result.current_values == [1]

        with pytest.raises(RateLimitError) as exc_info:
            await limiter.check_rate_limits_and_tier_fair_share_atomic(
                [rate_check],
                [],
                capacity_rate_checks=[capacity_check],
            )

        counter_key = (
            f"ratelimit:{rate_check.scope}:{rate_check.entity_id}:"
            f"{limiter._window_id(rate_check.window_seconds)}"
        )
        assert await redis.get(counter_key) == "1"
        assert await redis.hget(heatmap_key, field) == "1"
        assert await redis.zscore(rank_key, field) == 1
        assert int(await redis.get(total_key) or 0) >= 1
        assert exc_info.value.rate_limit_current == 1
        assert exc_info.value.rate_limit_attempted == 2
        assert exc_info.value.capacity_limit_hit_recorded is True
    finally:
        removed_hit = await redis.hdel(heatmap_key, field)
        await redis.zrem(rank_key, field)
        if removed_hit and await redis.exists(total_key):
            total = await redis.decr(total_key)
            if total <= 0:
                await redis.delete(total_key)
        keys = [key async for key in redis.scan_iter(match=f"*{unique}*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
