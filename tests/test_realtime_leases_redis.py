import pytest

from src.models.errors import ServiceUnavailableError
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
