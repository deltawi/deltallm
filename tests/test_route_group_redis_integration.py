from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from src.config import AppConfig
from src.db.route_groups import RouteGroupRuntimeSnapshot
from src.services.governance_invalidation import GovernanceInvalidationService
from src.services.route_groups import RouteGroupRuntimeCache, load_route_groups


class _MutableRouteGroupRepository:
    def __init__(self, groups: list[dict]) -> None:
        self.groups = groups
        self.calls = 0
        self.revision = 1

    async def get_runtime_revision(self) -> int:
        return self.revision

    async def load_runtime_snapshot(self) -> RouteGroupRuntimeSnapshot:
        self.calls += 1
        return RouteGroupRuntimeSnapshot(self.revision, list(self.groups))


@pytest.mark.skipif(
    not os.getenv("DELTALLM_TEST_REDIS_URL"),
    reason="DELTALLM_TEST_REDIS_URL is required for the Redis integration test",
)
@pytest.mark.asyncio
@pytest.mark.integration
async def test_route_group_notification_refreshes_remote_replica_from_durable_state() -> None:
    local_redis = Redis.from_url(os.environ["DELTALLM_TEST_REDIS_URL"], decode_responses=True)
    remote_redis = Redis.from_url(os.environ["DELTALLM_TEST_REDIS_URL"], decode_responses=True)
    channel = f"route-group-invalidation-{uuid4().hex}"
    cache_key = f"deltallm:test:route-group:{uuid4().hex}"
    cfg = AppConfig.model_validate({"router_settings": {"route_groups": []}})
    repository = _MutableRouteGroupRepository(
        [{"key": "route-v1", "mode": "chat", "enabled": True, "members": []}]
    )
    cache = RouteGroupRuntimeCache(
        remote_redis,
        l1_ttl_seconds=60,
        l2_ttl_seconds=300,
        cache_key=cache_key,
    )
    reloaded = asyncio.Event()
    observed: list[dict] = []

    async def reload_route_groups() -> None:
        await cache.invalidate(required_revision=repository.revision)
        groups, _ = await load_route_groups(repository, cfg, route_group_cache=cache)
        observed[:] = groups
        reloaded.set()

    local = GovernanceInvalidationService(redis_client=local_redis, channel_name=channel)
    remote = GovernanceInvalidationService(
        redis_client=remote_redis,
        route_group_reload=reload_route_groups,
        channel_name=channel,
        remote_apply_delay_seconds=0,
    )
    try:
        await remote_redis.delete(cache_key)
        await load_route_groups(repository, cfg, route_group_cache=cache)
        repository.groups = [{"key": "route-v2", "mode": "chat", "enabled": True, "members": []}]
        repository.revision = 2
        await local.start()
        await remote.start()

        assert await local.notify("route_groups") is True
        await asyncio.wait_for(reloaded.wait(), timeout=2)

        assert observed[0]["key"] == "route-v2"
        assert repository.calls == 2
    finally:
        await local.close()
        await remote.close()
        await remote_redis.delete(f"{cache_key}:r1", f"{cache_key}:r2")
        await local_redis.aclose()
        await remote_redis.aclose()


@pytest.mark.skipif(
    not os.getenv("DELTALLM_TEST_REDIS_URL"),
    reason="DELTALLM_TEST_REDIS_URL is required for the Redis integration test",
)
@pytest.mark.asyncio
@pytest.mark.integration
async def test_route_group_cache_recovers_after_real_redis_write_outage() -> None:
    redis = Redis.from_url(os.environ["DELTALLM_TEST_REDIS_URL"], decode_responses=True)

    class WriteOutageRedis:
        def __init__(self, delegate: Redis) -> None:
            self.delegate = delegate
            self.fail_setex = True

        async def setex(self, key: str, ttl: int, value: str) -> bool:
            if self.fail_setex:
                raise RuntimeError("simulated write outage")
            return bool(await self.delegate.setex(key, ttl, value))

        def __getattr__(self, name: str):  # noqa: ANN204
            return getattr(self.delegate, name)

    wrapper = WriteOutageRedis(redis)
    cache_key = f"deltallm:test:route-group:{uuid4().hex}"
    cfg = AppConfig.model_validate({"router_settings": {"route_groups": []}})
    repository = _MutableRouteGroupRepository(
        [{"key": "route-v1", "mode": "chat", "enabled": True, "members": []}]
    )
    cache = RouteGroupRuntimeCache(
        wrapper,
        l1_ttl_seconds=60,
        l2_ttl_seconds=300,
        cache_key=cache_key,
    )
    try:
        await redis.delete(cache_key)
        await load_route_groups(repository, cfg, route_group_cache=cache)
        repository.groups = [{"key": "route-v2", "mode": "chat", "enabled": True, "members": []}]
        repository.revision = 2

        assert await cache.invalidate(required_revision=2) is True
        groups, source = await load_route_groups(repository, cfg, route_group_cache=cache)
        assert source == "db"
        assert groups[0]["key"] == "route-v2"

        wrapper.fail_setex = False
        assert await cache.invalidate(required_revision=2) is True
        _, recovered_source = await load_route_groups(repository, cfg, route_group_cache=cache)
        assert recovered_source == "db"
        cache._l1_entry = None
        _, cached_source = await load_route_groups(repository, cfg, route_group_cache=cache)
        assert cached_source == "l2_cache"
    finally:
        await redis.delete(f"{cache_key}:r1", f"{cache_key}:r2")
        await redis.aclose()
