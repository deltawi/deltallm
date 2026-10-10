"""Own and close the disposable workload's local dependency clients."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
import os
from urllib.parse import urlparse

from prisma import Prisma
from redis.asyncio import Redis

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
CONNECT_SECONDS = 10
CLOSE_SECONDS = 5


def require_local_url(value: str, *, schemes: set[str]) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in schemes or parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError("The concurrency fixture requires explicit loopback dependencies")
    if parsed.scheme == "http" and (
        parsed.username or parsed.password or parsed.query or parsed.fragment
    ):
        raise ValueError("HTTP fixture URLs must not include credentials, queries, or fragments")
    return value


def fixture_database_url() -> str:
    value = require_local_url(os.environ["DATABASE_URL"], schemes={"postgresql", "postgres"})
    if urlparse(value).path != "/deltallm_concurrency":
        raise ValueError("The fixture database must be named deltallm_concurrency")
    return value


@asynccontextmanager
async def local_database() -> AsyncIterator[Prisma]:
    db = Prisma(datasource={"url": fixture_database_url()})
    try:
        async with asyncio.timeout(CONNECT_SECONDS):
            await db.connect(timeout=timedelta(seconds=CONNECT_SECONDS))
        yield db
    finally:
        # A failed connect can already have started the query-engine process.
        async with asyncio.timeout(CLOSE_SECONDS):
            await db.disconnect(timeout=timedelta(seconds=CLOSE_SECONDS))


async def _close_redis(client: Redis) -> None:
    async with asyncio.timeout(CLOSE_SECONDS):
        await client.aclose()


@dataclass(frozen=True)
class LocalDependencies:
    database: Prisma
    redis: Redis


@asynccontextmanager
async def local_dependencies() -> AsyncIterator[LocalDependencies]:
    redis_url = require_local_url(os.environ["REDIS_URL"], schemes={"redis", "rediss"})
    async with AsyncExitStack() as stack:
        db = await stack.enter_async_context(local_database())
        redis = Redis.from_url(
            redis_url,
            decode_responses=True,
            max_connections=2,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        stack.push_async_callback(_close_redis, redis)
        yield LocalDependencies(db, redis)
