from __future__ import annotations

import os
import time
from uuid import uuid4

import pytest

from redis.asyncio import Redis

from src.cache.backends.base import CacheEntry
from src.cache.backends.redis import RedisBackend
from src.cache.key_builder import CacheKeyBuilder

pytestmark = [pytest.mark.integration, pytest.mark.redis]


@pytest.mark.skipif(
    not os.getenv("DELTALLM_TEST_REDIS_URL"),
    reason="DELTALLM_TEST_REDIS_URL is required for the Redis integration test",
)
@pytest.mark.parametrize("legacy_version", ["v2", "v3"])
async def test_routing_identity_is_stable_across_clients_and_isolates_old_policy_and_version(
    legacy_version,
):
    writer = Redis.from_url(os.environ["DELTALLM_TEST_REDIS_URL"], decode_responses=True)
    reader = Redis.from_url(os.environ["DELTALLM_TEST_REDIS_URL"], decode_responses=True)
    write_backend, read_backend = RedisBackend(writer), RedisBackend(reader)
    salt = uuid4().hex
    first_builder, second_builder = (
        CacheKeyBuilder(custom_salt=salt),
        CacheKeyBuilder(custom_salt=salt),
    )

    def key(builder, policy, version="v4"):
        digest = builder.build_key_from_payload({}, "same custom key", routing_fingerprint=policy)
        return f"scope:key:integration:schema:{version}:mode:json:endpoint:chat:{digest}"

    original = key(first_builder, "policy-a")
    equivalent = key(second_builder, "policy-a")
    changed = key(second_builder, "policy-b")
    legacy = key(first_builder, "policy-a", version=legacy_version)
    entry = CacheEntry(response={"answer": "cached"}, model="group", cached_at=time.time(), ttl=60)
    try:
        await write_backend.set(legacy, entry)
        assert await read_backend.get(original) is None
        await write_backend.set(original, entry)
        assert original == equivalent
        loaded = await read_backend.get(equivalent)
        assert loaded is not None and loaded.response == entry.response
        assert await read_backend.get(changed) is None
        assert 0 < await reader.pttl(f"cache:{original}") <= 60_000
    finally:
        await writer.delete(f"cache:{original}", f"cache:{legacy}")
        await writer.aclose()
        await reader.aclose()
