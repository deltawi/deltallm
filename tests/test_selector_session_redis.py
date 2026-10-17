from __future__ import annotations

import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from src.router.redis_keys import RouterRedisKeyspace
from src.router.selection.session import SelectorSessionPin
from src.router.selector_session_store import RedisSelectorSessionStore

pytestmark = [
    pytest.mark.redis,
    pytest.mark.skipif(
        not os.getenv("DELTALLM_TEST_REDIS_URL"),
        reason="DELTALLM_TEST_REDIS_URL is required for the Redis integration test",
    ),
]


async def test_session_pins_are_shared_across_clients_with_a_sliding_ttl() -> None:
    writer_redis = Redis.from_url(os.environ["DELTALLM_TEST_REDIS_URL"], decode_responses=True)
    reader_redis = Redis.from_url(os.environ["DELTALLM_TEST_REDIS_URL"], decode_responses=True)
    keyspace = RouterRedisKeyspace(environment=f"test-{uuid4().hex[:8]}")
    writer = RedisSelectorSessionStore(writer_redis, keyspace=keyspace)
    reader = RedisSelectorSessionStore(reader_redis, keyspace=keyspace)
    key = uuid4().hex
    try:
        assert await reader.lookup(key) is None
        writer.remember(key, SelectorSessionPin(rank=0, deployment_id="economy"), ttl_seconds=60)
        await writer.close()
        assert await reader.lookup(key) == SelectorSessionPin(rank=0, deployment_id="economy")
        assert 0 < await reader_redis.ttl(keyspace.selector_session(key)) <= 60

        refresher = RedisSelectorSessionStore(writer_redis, keyspace=keyspace)
        refresher.remember(
            key, SelectorSessionPin(rank=1, deployment_id="quality"), ttl_seconds=600
        )
        await refresher.close()
        assert await reader.lookup(key) == SelectorSessionPin(rank=1, deployment_id="quality")
        assert await reader_redis.ttl(keyspace.selector_session(key)) > 60

        lower = RedisSelectorSessionStore(writer_redis, keyspace=keyspace)
        lower.remember(key, SelectorSessionPin(rank=0, deployment_id="economy"), ttl_seconds=900)
        await lower.close()
        assert await reader.lookup(key) == SelectorSessionPin(rank=1, deployment_id="quality")
        assert await reader_redis.ttl(keyspace.selector_session(key)) > 600

        await reader_redis.set(keyspace.selector_session(key), "{broken", ex=60)
        assert await reader.lookup(key) is None
        repair = RedisSelectorSessionStore(writer_redis, keyspace=keyspace)
        repair.remember(key, SelectorSessionPin(rank=0, deployment_id="economy"), ttl_seconds=60)
        await repair.close()
        assert await reader.lookup(key) == SelectorSessionPin(rank=0, deployment_id="economy")
    finally:
        await reader_redis.delete(keyspace.selector_session(key))
        await writer_redis.aclose()
        await reader_redis.aclose()


async def test_an_unreachable_redis_is_a_miss_and_a_dropped_write() -> None:
    redis = Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.05, socket_timeout=0.05)
    store = RedisSelectorSessionStore(redis, keyspace=RouterRedisKeyspace())
    try:
        assert await store.lookup("missing") is None
        store.remember("missing", SelectorSessionPin(rank=0, deployment_id="d"), ttl_seconds=60)
        await store.close()
    finally:
        await redis.aclose()
