import asyncio

import pytest

from src.models.errors import RateLimitError, ServiceUnavailableError
from src.services.limit_counter import LimitCounter, ParallelLimitCheck, _parallel_lease_key

from tests import test_selector_capacity_redis as fixtures

capacity_redis = fixtures.capacity_redis

pytestmark = [pytest.mark.integration, pytest.mark.redis]


async def test_deleted_owner_does_not_resurrect_or_extend_other_leases(capacity_redis):
    first, second, identity = capacity_redis
    limiter = LimitCounter(first, degraded_mode="fail_closed")
    leases = await limiter.acquire_parallel_leases(
        [ParallelLimitCheck("rt_a", identity, 2), ParallelLimitCheck("rt_b", identity, 2)],
        ttl_seconds=30,
    )
    key_a, key_b = (_parallel_lease_key(lease.scope, lease.entity_id) for lease in leases)
    before = await first.zscore(key_a, leases[0].token)
    await second.zrem(key_b, leases[1].token)
    with pytest.raises(ServiceUnavailableError):
        await limiter.refresh_parallel_leases(list(leases), ttl_seconds=60, require_owned=True)
    assert await first.zscore(key_a, leases[0].token) == before
    assert await first.zscore(key_b, leases[1].token) is None


async def test_expired_token_cannot_be_renewed(capacity_redis):
    first, _, identity = capacity_redis
    limiter = LimitCounter(first, degraded_mode="fail_closed")
    leases = await limiter.acquire_parallel_leases([ParallelLimitCheck("rt", identity, 1)])
    key = _parallel_lease_key("rt", identity)
    await first.zadd(key, {leases[0].token: 1})
    with pytest.raises(ServiceUnavailableError):
        await limiter.refresh_parallel_leases(list(leases), require_owned=True)
    assert await first.zscore(key, leases[0].token) == 1


async def test_renewal_uses_server_clock(capacity_redis, monkeypatch):
    first, _, identity = capacity_redis
    limiter = LimitCounter(first, degraded_mode="fail_closed")
    leases = await limiter.acquire_parallel_leases([ParallelLimitCheck("rt", identity, 1)])
    monkeypatch.setattr("src.services.limit_counter.time.time", lambda: 1)
    await limiter.refresh_parallel_leases(list(leases), ttl_seconds=30, require_owned=True)
    assert 28 <= await first.ttl(_parallel_lease_key("rt", identity)) <= 30


async def test_lost_shared_legacy_counter_fails_strict_renewal(capacity_redis):
    first, second, identity = capacity_redis
    limiter = LimitCounter(first, degraded_mode="fail_closed")
    lease = await limiter.acquire_legacy_parallel_lease("key", identity, 2)
    await second.delete(f"parallel:key:{identity}")
    with pytest.raises(ServiceUnavailableError):
        await limiter.refresh_legacy_parallel_lease(lease, require_owned=True)


async def _acquire(limiter, check, ttl, writer):
    if writer == "combined":
        result = await limiter.check_rate_limits_and_tier_fair_share_atomic(
            [], [], parallel_checks=[check], parallel_ttl_seconds=ttl
        )
        return result.parallel_leases
    return await limiter.acquire_parallel_leases([check], ttl_seconds=ttl)


@pytest.mark.parametrize("writer", ["standalone", "combined", "refresh", "strict_refresh"])
@pytest.mark.parametrize("long_first", [True, False])
async def test_short_owner_cannot_expire_long_owner(capacity_redis, writer, long_first):
    first, second, identity = capacity_redis
    http = LimitCounter(first, degraded_mode="fail_closed")
    websocket = LimitCounter(second, degraded_mode="fail_closed")
    check = ParallelLimitCheck("tier_org_model_parallel", identity, 2)
    if long_first:
        long = await _acquire(http, check, 300, "combined")
    short = await _acquire(websocket, check, 30, writer)
    if not long_first:
        long = await _acquire(http, check, 300, "combined")
    if writer in {"refresh", "strict_refresh"}:
        for _ in range(2):
            await websocket.refresh_parallel_leases(
                list(short), ttl_seconds=30, require_owned=writer == "strict_refresh"
            )
    key = _parallel_lease_key(check.scope, identity)
    long_expiry = await first.zscore(key, long[0].token)
    assert await first.pexpiretime(key) >= long_expiry
    await websocket.release_parallel_leases(list(short))
    assert await first.pexpiretime(key) >= long_expiry

    contenders = await asyncio.gather(
        *(websocket.acquire_parallel_leases([check], ttl_seconds=10) for _ in range(8)),
        return_exceptions=True,
    )
    assert sum(isinstance(result, tuple) for result in contenders) == 1
    assert sum(isinstance(result, RateLimitError) for result in contenders) == 7
    assert await first.pexpiretime(key) >= long_expiry
    assert await first.zscore(key, long[0].token) == long_expiry


@pytest.mark.parametrize("writer", ["standalone", "combined", "refresh", "strict_refresh"])
async def test_all_owned_lease_writers_use_server_time(capacity_redis, monkeypatch, writer):
    first, _, identity = capacity_redis
    limiter = LimitCounter(first, degraded_mode="fail_closed")
    check = ParallelLimitCheck("rt_clock", identity, 2)
    monkeypatch.setattr("src.services.limit_counter.time.time", lambda: 1)
    leases = await _acquire(limiter, check, 30, writer)
    if writer in {"refresh", "strict_refresh"}:
        await limiter.refresh_parallel_leases(
            list(leases), ttl_seconds=30, require_owned=writer == "strict_refresh"
        )
    key = _parallel_lease_key(check.scope, identity)
    seconds, micros = await first.time()
    remaining = await first.zscore(key, leases[0].token) - (seconds * 1000 + micros // 1000)
    assert 28_000 <= remaining <= 30_000
    assert 28 <= await first.ttl(key) <= 30


async def test_combined_admission_preserves_owners_after_script_cache_reset(capacity_redis):
    first, second, identity = capacity_redis
    limiter = LimitCounter(first, degraded_mode="fail_closed")
    check = ParallelLimitCheck("rt_script", identity, 3)
    long = await _acquire(limiter, check, 300, "combined")
    await second.script_flush()
    await _acquire(limiter, check, 30, "combined")
    key = _parallel_lease_key(check.scope, identity)
    assert await first.pexpiretime(key) >= await first.zscore(key, long[0].token)
