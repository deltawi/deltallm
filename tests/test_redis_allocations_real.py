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

pytestmark = [
    pytest.mark.redis,
    pytest.mark.asyncio,
    pytest.mark.skipif(not os.getenv("DELTALLM_TEST_REDIS_URL"), reason="test Redis URL required"),
]


async def test_real_bulk_exhaustion_and_timeout_preserve_critical_pool():
    settings = Settings(redis_url=os.environ["DELTALLM_TEST_REDIS_URL"])
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
