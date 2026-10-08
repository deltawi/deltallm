from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError

from src.db.repositories import KeyRecord
from src.models.errors import AuthenticationError, ServiceUnavailableError
from src.models.responses import UserAPIKeyAuth
from src.services.key_auth_cache import KeyAuthCache
from src.services.key_service import KeyService

pytestmark = [
    pytest.mark.redis,
    pytest.mark.skipif(
        not os.getenv("DELTALLM_TEST_REDIS_URL"), reason="DELTALLM_TEST_REDIS_URL is required"
    ),
]


@pytest.fixture
async def peers():
    local = Redis.from_url(os.environ["DELTALLM_TEST_REDIS_URL"], decode_responses=True)
    remote = Redis.from_url(os.environ["DELTALLM_TEST_REDIS_URL"], decode_responses=True)
    token_hash = uuid4().hex
    try:
        yield local, remote, token_hash
    finally:
        await local.delete(KeyAuthCache.key(token_hash))
        await local.aclose()
        await remote.aclose()


async def test_revocation_marker_defeats_delayed_reader_on_another_replica(peers):
    local, remote, token_hash = peers
    cache = KeyAuthCache(local)
    reader = KeyAuthCache(remote)
    lookup = await reader.lookup(token_hash)
    stale = UserAPIKeyAuth(api_key=token_hash, owner_account_id="account")
    await cache.revoke(token_hash, ttl_seconds=60)
    with pytest.raises(AuthenticationError):
        await reader.fill(token_hash, stale, ttl_seconds=60, deadline_ms=lookup.fill_deadline_ms)
    with pytest.raises(AuthenticationError):
        await reader.lookup(token_hash)
    await reader.invalidate([KeyAuthCache.key(token_hash)])
    with pytest.raises(AuthenticationError):
        await reader.lookup(token_hash)
    assert 60 <= await local.ttl(KeyAuthCache.key(token_hash)) <= 62


async def test_cache_fill_deadline_uses_redis_clock_and_cannot_install_late_allow(peers):
    local, _, token_hash = peers
    cache = KeyAuthCache(local)
    lookup = await cache.lookup(token_hash)
    with pytest.raises(ServiceUnavailableError):
        await cache.fill(
            token_hash,
            UserAPIKeyAuth(api_key=token_hash),
            ttl_seconds=60,
            deadline_ms=lookup.fill_deadline_ms - 1001,
        )
    assert await local.get(KeyAuthCache.key(token_hash)) is None


async def test_two_cold_readers_return_the_first_committed_cache_value(peers):
    local, remote, token_hash = peers
    first = KeyAuthCache(local)
    second = KeyAuthCache(remote)
    one = await first.lookup(token_hash)
    two = await second.lookup(token_hash)
    fresh = UserAPIKeyAuth(api_key=token_hash, owner_account_id="current-account")
    stale = UserAPIKeyAuth(api_key=token_hash, owner_account_id="stale-account")
    await first.fill(token_hash, fresh, ttl_seconds=60, deadline_ms=one.fill_deadline_ms)
    resolved = await second.fill(
        token_hash, stale, ttl_seconds=60, deadline_ms=two.fill_deadline_ms
    )
    assert resolved.owner_account_id == "current-account"


async def test_both_key_service_entrypoints_obey_markers_without_database_read(peers):
    local, remote, token_hash = peers

    class Repository:
        async def get_by_token(self, token):
            raise AssertionError("Revoked cache entry must deny before PostgreSQL")

    service = KeyService(Repository(), remote, salt="test", auth_cache_ttl_seconds=60)
    raw_key = "sk-" + token_hash
    actual_hash = service.hash_key(raw_key)
    try:
        await KeyAuthCache(local).revoke(actual_hash, ttl_seconds=60)
        with pytest.raises(AuthenticationError):
            await service.validate_key(raw_key)
        with pytest.raises(AuthenticationError):
            await service.get_auth_by_token_hash(actual_hash)
    finally:
        await local.delete(KeyAuthCache.key(actual_hash))


async def test_service_cannot_restore_allow_after_database_snapshot_races_revocation(peers):
    local, remote, token_hash = peers
    entered = asyncio.Event()
    release = asyncio.Event()

    class Repository:
        async def get_by_token(self, token):
            entered.set()
            await release.wait()
            return KeyRecord(token=token_hash)

    service = KeyService(Repository(), remote, auth_cache_ttl_seconds=60)
    pending = asyncio.create_task(service.get_auth_by_token_hash(token_hash))
    await asyncio.wait_for(entered.wait(), 1)
    await KeyAuthCache(local).revoke(token_hash, ttl_seconds=60)
    release.set()
    with pytest.raises(AuthenticationError):
        await pending
    assert service.primary_gate.active == 0


async def test_redis_outage_uses_bounded_primary_reads_and_never_installs_cache():
    class Offline:
        async def eval(self, *args):
            raise ConnectionError("test outage")

    class Repository:
        async def get_by_token(self, token):
            return KeyRecord(token=token, expires=datetime.now(UTC) + timedelta(seconds=60))

    service = KeyService(Repository(), Offline(), auth_cache_ttl_seconds=60)
    auth = await service.get_auth_by_token_hash("primary-only")
    assert auth.api_key == "primary-only" and service.primary_gate.active == 0
    for _ in range(4):
        await service.primary_gate.acquire(timeout_seconds=0.01)
    try:
        with pytest.raises(ServiceUnavailableError):
            await service.get_auth_by_token_hash("saturated")
    finally:
        for _ in range(4):
            await service.primary_gate.release()
