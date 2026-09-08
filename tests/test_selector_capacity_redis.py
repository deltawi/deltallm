import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from src.router.candidates import AttemptCapacity, AttemptCapacityLimit
from src.router.state import RedisStateBackend

pytestmark = [pytest.mark.integration, pytest.mark.redis]


@pytest.fixture
async def capacity_redis():
    url = os.getenv("DELTALLM_TEST_REDIS_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provision Redis for selector capacity tests")
        pytest.skip("DELTALLM_TEST_REDIS_URL is required")
    first, second = (
        Redis.from_url(url, decode_responses=True),
        Redis.from_url(url, decode_responses=True),
    )
    identity = "selector-capacity-" + uuid4().hex
    try:
        yield first, second, identity
    finally:
        keys = [key async for key in first.scan_iter(match=f"*{identity}*", count=100)]
        if keys:
            await first.delete(*keys)
        await first.aclose()
        await second.aclose()


async def test_atomic_consumption_and_owner_release_across_clients(capacity_redis):
    first, second, identity = capacity_redis
    owners = [RedisStateBackend(first), RedisStateBackend(second)]
    capacity = AttemptCapacity(
        limits=(AttemptCapacityLimit("rpm", 5, 1), AttemptCapacityLimit("tpm", 500, 100)),
        max_concurrency=2,
        require_shared=True,
    )
    permits = await asyncio.gather(
        *(owners[index % 2].acquire_attempt(identity, capacity) for index in range(16))
    )
    acquired = [permit for permit in permits if permit.acquired]
    assert len(acquired) == 2
    usage = await owners[0].get_usage(identity)
    assert usage["rpm"] == 2 and usage["tpm"] == 200
    await asyncio.gather(*(owners[1].release_attempt(permit) for permit in acquired))
    await asyncio.gather(*(owners[0].release_attempt(permit) for permit in acquired))
    assert await owners[1].get_active_requests(identity) == 0
    usage = await owners[1].get_usage(identity)
    assert usage["rpm"] == 2 and usage["tpm"] == 200


async def test_live_owner_replay_does_not_consume_twice_and_expiry_recovers(capacity_redis):
    first, second, identity = capacity_redis
    a, b = RedisStateBackend(first), RedisStateBackend(second)
    capacity = AttemptCapacity(
        limits=(AttemptCapacityLimit("rpm", 10, 1), AttemptCapacityLimit("tpm", 1000, 100)),
        max_concurrency=1,
        require_shared=True,
        owner_token=uuid4().hex,
    )
    permit = await a.acquire_attempt(identity, capacity, lease_ttl_seconds=1)
    replay = await b.acquire_attempt(identity, capacity, lease_ttl_seconds=1)
    assert replay.owner_token == permit.owner_token
    assert (await b.get_usage(identity))["rpm"] == 1
    # Advance the owner's Redis timestamp directly, with no arbitrary wall-clock sleep.
    await first.zadd(a.keyspace.attempt_owners(identity), {permit.owner_token: 0})
    new = await b.acquire_attempt(
        identity,
        AttemptCapacity(
            limits=capacity.limits, max_concurrency=1, require_shared=True, owner_token=uuid4().hex
        ),
        lease_ttl_seconds=1,
    )
    assert new.acquired
    await a.release_attempt(permit)
    assert await b.get_active_requests(identity) == 1
    await b.release_attempt(new)


async def test_server_clock_lease_expiry_and_client_reconnect_preserve_owner_isolation(
    capacity_redis,
):
    first, second, identity = capacity_redis
    a, b = RedisStateBackend(first), RedisStateBackend(second)
    bounds = AttemptCapacity(
        limits=(AttemptCapacityLimit("rpm", 100, 1),),
        max_concurrency=1,
        require_shared=True,
    )
    old = await a.acquire_attempt(identity, bounds, lease_ttl_seconds=1)
    assert old.acquired
    seconds, micros = await first.time()
    # This wait exercises the declared Redis TTL, not a timing workaround.
    remaining = (old.expires_at_ms - seconds * 1000 - micros / 1000) / 1000
    await asyncio.sleep(max(0, remaining) + 0.01)
    await second.connection_pool.disconnect()
    fresh = await b.acquire_attempt(identity, bounds, lease_ttl_seconds=1)
    assert fresh.acquired and fresh.owner_token != old.owner_token
    await a.release_attempt(old)
    assert await b.get_active_requests(identity) == 1
    await b.release_attempt(fresh)


async def test_transport_outage_fails_closed_and_reconnect_reuses_shared_state(
    capacity_redis, monkeypatch
):
    from redis.exceptions import ConnectionError
    from src.models.errors import ServiceUnavailableError

    first, second, identity = capacity_redis
    a, b = RedisStateBackend(first, degraded_mode="fail_open"), RedisStateBackend(second)
    bounds = AttemptCapacity(
        limits=(AttemptCapacityLimit("rpm", 10, 1),), max_concurrency=1, require_shared=True
    )
    held = await b.acquire_attempt(identity, bounds)
    original = first.eval

    async def unavailable(*args, **kwargs):
        raise ConnectionError("test-only transport interruption")

    monkeypatch.setattr(first, "eval", unavailable)
    with pytest.raises(ServiceUnavailableError):
        await a.acquire_attempt(identity, bounds)
    monkeypatch.setattr(first, "eval", original)
    await first.connection_pool.disconnect()
    assert not (await a.acquire_attempt(identity, bounds)).acquired
    await b.release_attempt(held)
    recovered = await a.acquire_attempt(identity, bounds)
    assert recovered.acquired
    await a.release_attempt(recovered)
