from contextlib import AsyncExitStack
import os
from uuid import uuid4

import pytest
from redis.exceptions import OutOfMemoryError

from src.config import GeneralSettings, Settings
from src.models.errors import ServiceUnavailableError
from src.redis_runtime import build_redis_client
from src.services.limit_counter import LimitCounter

pytestmark = pytest.mark.redis


@pytest.fixture
async def isolated_memory_servers():
    critical_url = os.getenv("DELTALLM_TEST_REDIS_CRITICAL_MEMORY_URL")
    cache_url = os.getenv("DELTALLM_TEST_REDIS_CACHE_MEMORY_URL")
    if not critical_url or not cache_url:
        if os.getenv("CI"):
            pytest.fail("CI must provision separate critical/cache memory-test Redis servers")
        pytest.skip("Two dedicated empty Redis servers are required for eviction qualification")
    settings = Settings(redis_url=critical_url, redis_bulk_url=cache_url)
    async with AsyncExitStack() as stack:
        critical = build_redis_client(settings, GeneralSettings(), allocation="critical")
        cache = build_redis_client(settings, GeneralSettings(), allocation="bulk")
        for client in (critical, cache):
            stack.push_async_callback(client.aclose)
        assert (await critical.info("server"))["run_id"] != (await cache.info("server"))["run_id"]
        # Never alter shared test/development caches. These URLs must designate
        # dedicated instances, not just different database numbers on one server.
        assert await critical.dbsize() == await cache.dbsize() == 0
        originals = [
            await client.config_get("maxmemory", "maxmemory-policy") for client in (critical, cache)
        ]
        try:
            yield critical, cache
        finally:
            for client, original in zip((critical, cache), originals):
                await client.config_set("maxmemory", original["maxmemory"])
                await client.config_set("maxmemory-policy", original["maxmemory-policy"])


async def test_cache_eviction_cannot_evict_critical_keys_and_critical_oom_fails_closed(
    isolated_memory_servers,
):
    critical, cache = isolated_memory_servers
    prefix = "allocation-memory:" + uuid4().hex
    critical_keys, cache_keys = [], []
    try:
        critical_memory = int((await critical.info("memory"))["used_memory"])
        cache_memory = int((await cache.info("memory"))["used_memory"])
        await critical.config_set("maxmemory-policy", "noeviction")
        await critical.config_set("maxmemory", critical_memory + 2 * 1024 * 1024)
        await cache.config_set("maxmemory-policy", "allkeys-lru")
        await cache.config_set("maxmemory", cache_memory + 1024 * 1024)
        critical_evictions = int((await critical.info("stats"))["evicted_keys"])
        cache_evictions = int((await cache.info("stats"))["evicted_keys"])
        lease_key = prefix + ":lease"
        critical_keys.append(lease_key)
        await critical.set(lease_key, "owner-token", ex=300)
        for index in range(256):
            key = prefix + f":cache:{index}"
            cache_keys.append(key)
            await cache.set(key, "x" * 32768, ex=300)
        assert int((await cache.info("stats"))["evicted_keys"]) > cache_evictions
        assert await critical.get(lease_key) == "owner-token"
        assert 0 < await critical.ttl(lease_key) <= 300
        assert int((await critical.info("stats"))["evicted_keys"]) == critical_evictions

        for index in range(128):
            key = prefix + f":critical:{index}"
            critical_keys.append(key)
            try:
                await critical.set(key, "x" * 65536, ex=300)
            except OutOfMemoryError:
                break
        else:
            pytest.fail("Bounded critical fill did not reach noeviction pressure")
        used = int((await critical.info("memory"))["used_memory"])
        await critical.config_set("maxmemory", used - 65536)
        with pytest.raises(ServiceUnavailableError):
            await LimitCounter(critical, degraded_mode="fail_closed").check_rate_limit(
                "key", prefix, 1
            )
        assert await critical.get(lease_key) == "owner-token"
        assert int((await critical.info("stats"))["evicted_keys"]) == critical_evictions
    finally:
        if critical_keys:
            await critical.delete(*critical_keys)
        if cache_keys:
            await cache.delete(*cache_keys)
