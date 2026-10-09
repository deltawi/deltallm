from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from src.db.repositories import KeyRecord
from src.models.errors import AuthenticationError
from src.services.auth_fallback import AuthFallbackLimits
from src.services.key_service import KeyService

pytestmark = [
    pytest.mark.redis,
    pytest.mark.asyncio,
    pytest.mark.skipif(not os.getenv("DELTALLM_TEST_REDIS_URL"), reason="test Redis URL required"),
]


async def test_auth_cache_shares_ttl_and_revocation_across_clients() -> None:
    class Repository:
        calls = 0
        revoked = False

        async def get_by_token(self, token_hash):
            self.calls += 1
            return None if self.revoked else KeyRecord(token=token_hash)

    async with AsyncExitStack() as stack:
        clients = [
            Redis.from_url(
                os.environ["DELTALLM_TEST_REDIS_URL"],
                max_connections=4,
                socket_timeout=1,
                socket_connect_timeout=1,
            )
            for _ in range(2)
        ]
        for client in clients:
            stack.push_async_callback(client.aclose)
        repo = Repository()
        salt = uuid4().hex
        services = [
            KeyService(repo, client, salt=salt, auth_cache_ttl_seconds=1) for client in clients
        ]
        key = services[0]._cache_key(services[0].hash_key("sk-fixture"))
        stack.push_async_callback(clients[0].delete, key)
        for service in services:
            stack.push_async_callback(service.close)
        await services[0].validate_key("sk-fixture")
        await services[1].validate_key("sk-fixture")
        assert repo.calls == 1
        assert 0 < await clients[1].pttl(key) <= 1000
        repo.revoked = True
        await services[1].invalidate_key_cache_by_hash(services[1].hash_key("sk-fixture"))
        with pytest.raises(AuthenticationError):
            await services[0].validate_key("sk-fixture")
        assert repo.calls == 2


async def test_real_redis_cold_key_collapses_with_bounded_callers() -> None:
    class Repository:
        calls = 0
        release = asyncio.Event()

        async def get_by_token(self, token_hash):
            self.calls += 1
            await self.release.wait()
            return KeyRecord(token=token_hash)

    async with AsyncExitStack() as stack:
        client = Redis.from_url(
            os.environ["DELTALLM_TEST_REDIS_URL"],
            max_connections=40,
            socket_timeout=1,
            socket_connect_timeout=1,
        )
        stack.push_async_callback(client.aclose)
        repo = Repository()
        service = KeyService(
            repo,
            client,
            salt=uuid4().hex,
            fallback_limits=AuthFallbackLimits(timeout_seconds=2, cache_timeout_seconds=1),
        )
        key = service._cache_key(service.hash_key("sk-fixture"))
        stack.push_async_callback(client.delete, key)
        stack.push_async_callback(service.close)
        tasks = [asyncio.create_task(service.validate_key("sk-fixture")) for _ in range(20)]
        try:
            async with asyncio.timeout(1):
                while service.fallback.callers != 20:
                    await asyncio.sleep(0.001)
            assert repo.calls == service.fallback.size == 1
            repo.release.set()
            await asyncio.gather(*tasks)
            assert repo.calls == 1
            assert await client.exists(key)
        finally:
            repo.release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
