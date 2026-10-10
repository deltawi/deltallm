from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.redis_runtime import build_redis_client
from src.services.prompt_registry import PromptRegistryService
from tests.test_prompt_cache_performance import _CountingRepository

pytestmark = pytest.mark.redis


@pytest.mark.asyncio
async def test_real_pipeline_ttls_shared_fill_and_allocation_recovery():
    url = os.getenv("DELTALLM_TEST_REDIS_URL")
    if not url:
        pytest.fail("DELTALLM_TEST_REDIS_URL is required")
    config = SimpleNamespace(redis_url=url, redis_cache_max_connections=1)
    redis = build_redis_client(config, config, allocation="cache")
    prefix = uuid4().hex
    scopes = [(scope, prefix) for scope in ("user", "api_key", "team", "organization", "group")]
    first = PromptRegistryService(
        repository=_CountingRepository(), redis_client=redis, negative_l2_ttl_seconds=5
    )
    second_repo = _CountingRepository()
    second = PromptRegistryService(
        repository=second_repo, redis_client=redis, negative_l2_ttl_seconds=5
    )
    keys = [first._binding_cache_key(*scope) for scope in scopes]
    try:
        assert await first._resolve_binding_chain(scopes) == [None] * 5
        ttls = [await redis.ttl(key) for key in keys]
        assert all(0 < ttl <= 5 for ttl in ttls)
        assert await second._resolve_binding_chain(scopes) == [None] * 5
        assert second_repo.binding_queries == 0
        assert redis.connection_pool.gate.active == redis.connection_pool.gate.waiters == 0
        # Saturate the one-connection cache allocation. Resolution still uses its
        # durable repository, then the same client recovers when capacity returns.
        held = await redis.connection_pool.get_connection()
        try:
            first._invalidate_local_caches()
            assert await first._resolve_binding_chain(scopes) == [None] * 5
        finally:
            await redis.connection_pool.release(held)
        first._invalidate_local_caches()
        assert await first._resolve_binding_chain(scopes) == [None] * 5
        assert redis.connection_pool.gate.active == 0
    finally:
        await first.shutdown()
        await second.shutdown()
        await redis.delete(*keys)
        await redis.aclose()
