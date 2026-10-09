from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError

from src.db.repositories import KeyRecord
from src.models.errors import (
    AuthenticationError,
    AuthenticationUnavailableError,
    ServiceUnavailableError,
)
from src.services.auth_fallback import AuthFallbackLimits
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
        await local.delete(*(f"key:v{version}:{token_hash}" for version in (4, 5, 6, 7)))
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
    assert json.loads(await local.get(f"key:v5:{token_hash}")) == {
        "cache_version": 5,
        "cache_kind": "revoked",
    }


@pytest.mark.parametrize("warm", [False, True])
async def test_older_console_replica_revocation_blocks_new_lookup_and_delayed_fill(peers, warm):
    local, remote, token_hash = peers
    reader = KeyAuthCache(remote)
    lookup = await reader.lookup(token_hash)
    auth = UserAPIKeyAuth(api_key=token_hash, key_output_tpm_limit=123)
    if warm:
        await reader.fill(token_hash, auth, ttl_seconds=60, deadline_ms=lookup.fill_deadline_ms)
    # An older Console replica publishes only its v5 tombstone.
    await local.setex(
        f"key:v5:{token_hash}",
        62,
        json.dumps({"cache_version": 5, "cache_kind": "revoked"}),
    )
    with pytest.raises(AuthenticationError):
        await reader.lookup(token_hash)
    with pytest.raises(AuthenticationError):
        await reader.fill(token_hash, auth, ttl_seconds=60, deadline_ms=lookup.fill_deadline_ms)


@pytest.mark.parametrize("observed", [False, True])
async def test_shorter_legacy_revocation_cannot_restore_longer_cached_allow(peers, observed):
    local, remote, token_hash = peers

    class Repository:
        record = KeyRecord(token=token_hash, output_tpm_limit=123)
        calls = 0

        async def get_by_token(self, token):
            assert token == token_hash
            self.calls += 1
            return self.record

    repository = Repository()
    service = KeyService(repository, remote, auth_cache_ttl_seconds=30)
    assert (await service.get_auth_by_token_hash(token_hash)).key_output_tpm_limit == 123
    repository.record = None  # The old replica commits the durable key removal first.
    await local.setex(
        f"key:v5:{token_hash}",
        3,  # A valid old replica uses a one-second auth TTL plus its two-second guard.
        json.dumps({"cache_version": 5, "cache_kind": "revoked"}),
    )
    if observed:
        with pytest.raises(AuthenticationError):
            await service.get_auth_by_token_hash(token_hash)
    await asyncio.sleep(3.1)
    assert await local.get(f"key:v5:{token_hash}") is None
    assert await local.ttl(KeyAuthCache.key(token_hash)) > 0
    with pytest.raises(AuthenticationError):
        await service.get_auth_by_token_hash(token_hash)
    assert repository.calls == 2
    assert await local.get(KeyAuthCache.key(token_hash)) is None


@pytest.mark.parametrize("replacement", [None, "{}", '"invalid"', "old_allow", "new_guard"])
async def test_missing_or_replaced_legacy_guard_requires_fresh_primary_policy(peers, replacement):
    local, remote, token_hash = peers

    class Repository:
        limit = 123
        calls = 0

        async def get_by_token(self, token):
            self.calls += 1
            return KeyRecord(token=token, output_tpm_limit=self.limit)

    repository = Repository()
    service = KeyService(repository, remote, auth_cache_ttl_seconds=30)
    await service.get_auth_by_token_hash(token_hash)
    guard_key = f"key:v5:{token_hash}"
    if replacement is None:
        await local.delete(guard_key)
    else:
        if replacement in ("old_allow", "new_guard"):
            guard = json.loads(await local.get(guard_key))
            if replacement == "old_allow":
                guard.pop("cache_guard")
            else:
                guard["cache_guard"] = uuid4().hex
            replacement = json.dumps(guard)
        await local.setex(guard_key, 30, replacement)
    repository.limit = 456
    for source in ("database", "redis"):
        auth = await service.get_auth_by_token_hash(token_hash)
        assert auth.key_output_tpm_limit == 456 and auth.metadata["auth_cache_source"] == source
    assert repository.calls == 2
    current = json.loads(await local.get(KeyAuthCache.key(token_hash)))
    guard = json.loads(await local.get(guard_key))
    assert current["cache_guard"] == guard["cache_guard"]
    assert current["auth"]["key_output_tpm_limit"] == 456
    assert "key_output_tpm_limit" not in guard["auth"]
    assert current["auth"]["api_key"] == guard["auth"]["api_key"]
    assert abs(await local.pttl(KeyAuthCache.key(token_hash)) - await local.pttl(guard_key)) < 100


async def test_pre_fix_v7_allow_is_replaced_from_primary_and_old_v5_reader_remains_compatible(
    peers,
):
    local, remote, token_hash = peers
    stale = UserAPIKeyAuth(api_key=token_hash, key_output_tpm_limit=123)
    for version in (5, 7):
        await local.setex(
            f"key:v{version}:{token_hash}",
            300,
            json.dumps(
                {
                    "cache_version": version,
                    "cache_kind": "allow",
                    "auth": stale.model_dump(mode="json"),
                }
            ),
        )

    class Repository:
        calls = 0

        async def get_by_token(self, token):
            self.calls += 1
            return KeyRecord(token=token, output_tpm_limit=456, owner_account_id="owner")

    repository = Repository()
    service = KeyService(repository, remote, auth_cache_ttl_seconds=30)
    assert (await service.get_auth_by_token_hash(token_hash)).key_output_tpm_limit == 456
    assert (await service.get_auth_by_token_hash(token_hash)).key_output_tpm_limit == 456
    assert repository.calls == 1
    legacy = json.loads(await local.get(f"key:v5:{token_hash}"))
    assert legacy["cache_version"] == 5 and legacy["cache_kind"] == "allow"
    assert UserAPIKeyAuth.model_validate(legacy["auth"]).owner_account_id == "owner"


@pytest.mark.parametrize("version", [4, 6])
async def test_revocation_clears_raw_legacy_auth_even_when_its_ttl_exceeds_rollback_wait(
    peers, version
):
    local, remote, token_hash = peers
    legacy_key = f"key:v{version}:{token_hash}"
    legacy = UserAPIKeyAuth(api_key=token_hash, owner_account_id="owner")
    await remote.setex(legacy_key, 300, legacy.model_dump_json())
    assert (
        UserAPIKeyAuth.model_validate_json(await remote.get(legacy_key)).owner_account_id == "owner"
    )
    assert await remote.ttl(legacy_key) > 61
    await KeyAuthCache(local).revoke(token_hash, ttl_seconds=1)
    assert await remote.get(legacy_key) is None
    with pytest.raises(AuthenticationError):
        await KeyAuthCache(remote).lookup(token_hash)


async def test_policy_invalidation_drops_legacy_allow_but_preserves_revocations(peers):
    local, remote, token_hash = peers
    cache = KeyAuthCache(local)
    await cache.revoke(token_hash, ttl_seconds=60)
    for version, payload in ((4, '"legacy"'), (6, "{}")):
        await local.setex(f"key:v{version}:{token_hash}", 60, payload)
    service = KeyService(object(), remote)
    await service.invalidate_key_cache_by_hash(token_hash)
    for version in (4, 6):
        assert await local.get(f"key:v{version}:{token_hash}") is None
    for version in (5, 7):
        assert (
            json.loads(await local.get(f"key:v{version}:{token_hash}"))["cache_kind"] == "revoked"
        )
    with pytest.raises(AuthenticationError):
        await service.get_auth_by_token_hash(token_hash)


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
        await local.delete(KeyAuthCache.key(actual_hash), f"key:v5:{actual_hash}")


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
    assert service.fallback.gate.active == 0


async def test_redis_outage_uses_bounded_primary_reads_and_never_installs_cache():
    class Offline:
        async def eval(self, *args):
            raise ConnectionError("test outage")

    class Repository:
        async def get_by_token(self, token):
            return KeyRecord(token=token, expires=datetime.now(UTC) + timedelta(seconds=60))

    service = KeyService(
        Repository(),
        Offline(),
        auth_cache_ttl_seconds=60,
        fallback_limits=AuthFallbackLimits(max_active=4, max_waiters=0),
    )
    auth = await service.get_auth_by_token_hash("primary-only")
    assert auth.api_key == "primary-only" and service.fallback.gate.active == 0
    for _ in range(4):
        await service.fallback.gate.acquire(timeout_seconds=0.01)
    try:
        with pytest.raises(AuthenticationUnavailableError):
            await service.get_auth_by_token_hash("saturated")
    finally:
        for _ in range(4):
            await service.fallback.gate.release()
