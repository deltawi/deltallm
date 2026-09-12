from __future__ import annotations

from contextlib import AsyncExitStack
import os
from uuid import uuid4

import pytest
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
    TimeoutError as RedisTimeoutError,
)

from src.config import GeneralSettings, Settings
from src.redis_runtime import build_redis_client

pytestmark = [pytest.mark.redis, pytest.mark.asyncio]


@pytest.fixture
def redis_settings():
    url = os.getenv("DELTALLM_TEST_REDIS_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("CI must provide DELTALLM_TEST_REDIS_URL for Redis allocation tests")
        pytest.skip("test Redis URL required")
    return Settings(redis_url=url)


async def test_real_bulk_exhaustion_and_timeout_preserve_critical_pool(redis_settings):
    settings = redis_settings
    general = GeneralSettings(
        redis_critical_max_connections=1,
        redis_bulk_max_connections=1,
        redis_socket_timeout_seconds=0.1,
        redis_acquisition_timeout_seconds=1,
    )
    async with AsyncExitStack() as stack:
        critical = build_redis_client(settings, general, allocation="critical")
        stack.push_async_callback(critical.aclose)
        bulk = build_redis_client(settings, general, allocation="bulk")
        stack.push_async_callback(bulk.aclose)
        held = await bulk.connection_pool.get_connection()
        try:
            with pytest.raises(RedisConnectionError, match="full"):
                await bulk.ping()
            assert await critical.ping()
        finally:
            await bulk.connection_pool.release(held)
        # An empty unique list makes the server wait; the socket deadline must
        # disconnect it, return the slot, and allow a fresh command to recover.
        with pytest.raises(RedisTimeoutError):
            await bulk.blpop("allocation-test:" + uuid4().hex, timeout=0)
        assert bulk.connection_pool.gate.active == 0
        assert await bulk.ping()
        assert await critical.ping()


async def test_idle_pubsub_survives_multiple_socket_deadlines_and_receives_update(redis_settings):
    import asyncio

    settings = redis_settings
    general = GeneralSettings(redis_socket_timeout_seconds=0.02)
    async with AsyncExitStack() as stack:
        client = build_redis_client(settings, general, allocation="critical")
        stack.push_async_callback(client.aclose)
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        stack.push_async_callback(pubsub.aclose)
        channel = "allocation-idle:" + uuid4().hex
        await pubsub.subscribe(channel)
        stream = pubsub.listen()
        task = asyncio.create_task(anext(stream))
        try:
            # Deliberately cross four native socket deadlines with no messages.
            await asyncio.sleep(0.08)
            assert not task.done()
            await client.publish(channel, "updated")
            async with asyncio.timeout(1):
                message = await task
            assert message["data"] == "updated"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await stream.aclose()
