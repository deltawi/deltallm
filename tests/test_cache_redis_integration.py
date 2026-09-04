from __future__ import annotations

import json
import os
import time
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from src.cache.backends.base import CacheEntry
from src.cache.backends.redis import RedisBackend
from src.cache.streaming import StreamingCacheHandler


@pytest.mark.skipif(
    not os.getenv("DELTALLM_TEST_REDIS_URL"),
    reason="DELTALLM_TEST_REDIS_URL is required for the Redis integration test",
)
async def test_stream_cache_fields_round_trip_across_redis_clients_with_ttl() -> None:
    local_redis = Redis.from_url(
        os.environ["DELTALLM_TEST_REDIS_URL"],
        decode_responses=True,
    )
    remote_redis = Redis.from_url(
        os.environ["DELTALLM_TEST_REDIS_URL"],
        decode_responses=True,
    )
    key = f"stream-cache-integration-{uuid4().hex}"
    local_backend = RedisBackend(local_redis)
    remote_backend = RedisBackend(remote_redis)
    lines = [
        'data: {"id":"chatcmpl-redis","choices":[{"index":0,'
        '"delta":{"reasoning_content":"think"},"finish_reason":null}]}',
        'data: {"id":"chatcmpl-redis","choices":[{"index":0,'
        '"delta":{"content":"answer"},"finish_reason":"stop"}]}',
    ]
    usage_line = (
        'data: {"id":"chatcmpl-redis","choices":[],"usage":'
        '{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}'
    )
    entry = CacheEntry(
        response={
            "id": "chatcmpl-redis",
            "model": "group",
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
        model="group",
        cached_at=time.time(),
        ttl=60,
        stream_lines=lines,
        stream_usage_line=usage_line,
    )

    try:
        await local_backend.set(key, entry, 60)

        loaded = await remote_backend.get(key)

        assert loaded is not None
        assert loaded.stream_lines == lines
        assert loaded.stream_usage_line == usage_line
        ttl_ms = await remote_redis.pttl(f"cache:{key}")
        assert 0 < ttl_ms <= 60_000
        handler = StreamingCacheHandler(remote_backend)
        replayed = [
            line async for line in handler.reconstruct_sse_stream(loaded, include_usage=True)
        ]
        assert replayed == [
            *(f"{line}\n\n" for line in lines),
            f"{usage_line}\n\n",
            "data: [DONE]\n\n",
        ]
    finally:
        await local_backend.delete(key)
        await local_redis.aclose()
        await remote_redis.aclose()


@pytest.mark.skipif(
    not os.getenv("DELTALLM_TEST_REDIS_URL"),
    reason="DELTALLM_TEST_REDIS_URL is required for the Redis integration test",
)
async def test_legacy_redis_stream_cache_entry_is_not_replayable() -> None:
    redis = Redis.from_url(
        os.environ["DELTALLM_TEST_REDIS_URL"],
        decode_responses=True,
    )
    backend = RedisBackend(redis)
    key = f"legacy-stream-cache-integration-{uuid4().hex}"
    payload = {
        "response": {"id": "legacy", "usage": {}},
        "model": "group",
        "cached_at": time.time(),
        "ttl": 60,
    }

    try:
        await redis.setex(f"cache:{key}", 60, json.dumps(payload))
        loaded = await backend.get(key)

        assert loaded is not None
        assert StreamingCacheHandler(backend).can_replay(loaded) is False
    finally:
        await backend.delete(key)
        await redis.aclose()
