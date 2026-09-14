"""Measure key lookup budgets against the seeded, disposable PostgreSQL/Redis fixture."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
from dataclasses import asdict
import json
import os
from pathlib import Path

from redis.asyncio import Redis

from src.db.allocated_client import AllocatedPrisma, DatabaseOwner
from src.db.allocation_config import DatabasePolicy
from src.db.repositories import KeyRepository
from src.services.auth_fallback import AuthFallbackLimits
from src.services.key_service import KeyService
from tests.performance.gateway_concurrency_dependencies import (
    fixture_database_url,
    require_local_url,
)
from tests.performance.gateway_concurrency_fixture import fixture_key


class CountedRepository(KeyRepository):
    calls = 0

    async def get_by_token(self, token_hash):
        self.calls += 1
        return await super().get_by_token(token_hash)


class CountedCache:
    def __init__(self, client):
        self.client = client
        self.reads = self.writes = 0
        self.unavailable = False

    async def get(self, key):
        self.reads += 1
        if self.unavailable:
            raise ConnectionError("injected cache outage")
        return await self.client.get(key)

    async def setex(self, key, ttl, value):
        self.writes += 1
        if self.unavailable:
            raise ConnectionError("injected cache outage")
        return await self.client.setex(key, ttl, value)


async def measure() -> dict:
    policy = DatabasePolicy("foreground", 8, 0.2, 1, 0.2, 2)
    limits = AuthFallbackLimits()
    async with AsyncExitStack() as stack:
        owner = DatabaseOwner(policy)
        db = AllocatedPrisma(
            datasource={"url": policy.connection_url(fixture_database_url())}, allocation=owner
        )
        stack.push_async_callback(db.disconnect)
        stack.push_async_callback(owner.close)
        await db.connect()
        redis = Redis.from_url(
            require_local_url(os.environ["REDIS_URL"], schemes={"redis", "rediss"}),
            max_connections=40,
            socket_timeout=1,
            socket_connect_timeout=1,
        )
        stack.push_async_callback(redis.aclose)
        repo, cache = CountedRepository(db), CountedCache(redis)
        service = KeyService(
            repo, cache, salt=os.environ["DELTALLM_SALT_KEY"], fallback_limits=limits
        )
        stack.push_async_callback(service.close)
        key = fixture_key()
        cache_key = service._cache_key(service.hash_key(key))
        stack.push_async_callback(redis.delete, cache_key)
        rows = []
        for case in ("warm", "cold", "cold_concurrent", "cache_outage"):
            await redis.delete(cache_key)
            await service.validate_key(key)
            repo.calls = cache.reads = cache.writes = 0
            cache.unavailable = case == "cache_outage"
            requests = 20 if case == "cold_concurrent" else 100
            if case == "cold_concurrent":
                await redis.delete(cache_key)
                results = await asyncio.gather(
                    *(service.validate_key(key) for _ in range(requests))
                )
            else:
                results = []
                for _ in range(requests):
                    if case == "cold":
                        await redis.delete(cache_key)
                    results.append(await service.validate_key(key))
            cache.unavailable = False
            assert all(result.api_key == service.hash_key(key) for result in results)
            rows.append(
                {
                    "case": case,
                    "requests": requests,
                    "cache_read_attempts": cache.reads,
                    "cache_write_attempts": cache.writes,
                    "key_queries": repo.calls,
                    "successes": len(results),
                    "auth_tasks_after": service.fallback.size,
                    "database_slots_after": owner.gate.active,
                }
            )
        return {"database_policy": asdict(policy), "auth_limits": asdict(limits), "cases": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(measure())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
