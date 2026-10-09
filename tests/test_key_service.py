from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from src.db.repositories import KeyRecord, KeyRepository
from src.services.key_service import KeyService


class InMemoryRepo:
    def __init__(self, records: dict[str, KeyRecord]) -> None:
        self.records = records
        self.calls = 0

    async def get_by_token(self, token_hash: str) -> KeyRecord | None:
        self.calls += 1
        return self.records.get(token_hash)


class ScopedRepo:
    def __init__(self, tokens: list[str]) -> None:
        self.prisma = self
        self.tokens = tokens

    async def query_raw(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        del args, kwargs
        return [{"token": token} for token in self.tokens]


class RecordingRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.delete_calls: list[tuple[str, ...]] = []

    async def get(self, key: str):
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self.store[key] = value
        self.ttls[key] = ttl

    async def delete(self, *keys: str):
        self.delete_calls.append(tuple(keys))
        for key in keys:
            self.store.pop(key, None)
            self.ttls.pop(key, None)


class LifecycleRowPrisma:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row
        self.sql = ""

    async def query_raw(self, sql: str, *params: object) -> list[dict[str, object]]:
        del params
        self.sql = sql
        return [self.row]


@pytest.mark.asyncio
async def test_key_repository_marks_broken_organization_reference_missing() -> None:
    prisma = LifecycleRowPrisma(
        {
            "token": "token-hash",
            "organization_id": "missing-org",
            "organization_lifecycle_state": None,
        }
    )

    record = await KeyRepository(prisma).get_by_token("token-hash")

    assert record is not None
    assert record.organization_lifecycle_state == "missing"
    assert "WHEN o.organization_id IS NULL THEN 'missing'" in prisma.sql


@pytest.mark.asyncio
async def test_key_repository_keeps_explicitly_unowned_scope_active() -> None:
    prisma = LifecycleRowPrisma(
        {
            "token": "token-hash",
            "organization_id": None,
            "organization_lifecycle_state": None,
        }
    )

    record = await KeyRepository(prisma).get_by_token("token-hash")

    assert record is not None
    assert record.organization_lifecycle_state == "active"


@pytest.mark.asyncio
async def test_key_cache_invalidation_by_hash() -> None:
    salt = "test-salt"
    raw_key = "sk-cache-test"
    token_hash = hashlib.sha256(f"{salt}:{raw_key}".encode("utf-8")).hexdigest()
    repo = InMemoryRepo(
        {
            token_hash: KeyRecord(
                token=token_hash,
                expires=datetime.now(tz=UTC) + timedelta(hours=1),
            )
        }
    )
    redis = RecordingRedis()
    service = KeyService(repository=repo, redis_client=redis, salt=salt, auth_cache_ttl_seconds=300)

    await service.validate_key(raw_key)
    assert repo.calls == 1

    await service.validate_key(raw_key)
    assert repo.calls == 1

    await service.invalidate_key_cache_by_hash(token_hash)
    await service.validate_key(raw_key)
    assert repo.calls == 2


@pytest.mark.asyncio
async def test_key_scope_invalidation_batches_redis_deletes() -> None:
    tokens = [f"token-{index}" for index in range(501)]
    repo = ScopedRepo(tokens)
    redis = RecordingRedis()
    service = KeyService(repository=repo, redis_client=redis)

    invalidated = await service.invalidate_keys_for_org("org-1")

    assert invalidated == 501
    assert [len(call) for call in redis.delete_calls] == [500, 1]
    assert redis.delete_calls[0][0] == "key:v4:token-0"
    assert redis.delete_calls[1][0] == "key:v4:token-500"


@pytest.mark.asyncio
async def test_key_cache_ttl_respects_configured_limit() -> None:
    salt = "test-salt"
    raw_key = "sk-ttl-test"
    token_hash = hashlib.sha256(f"{salt}:{raw_key}".encode("utf-8")).hexdigest()
    repo = InMemoryRepo(
        {
            token_hash: KeyRecord(
                token=token_hash,
                expires=datetime.now(tz=UTC) + timedelta(hours=1),
            )
        }
    )
    redis = RecordingRedis()
    service = KeyService(repository=repo, redis_client=redis, salt=salt, auth_cache_ttl_seconds=300)

    await service.validate_key(raw_key)
    cache_key = f"key:v4:{token_hash}"
    assert redis.ttls[cache_key] == 300


@pytest.mark.asyncio
async def test_key_cache_ttl_capped_by_key_expiry() -> None:
    salt = "test-salt"
    raw_key = "sk-expiring-test"
    token_hash = hashlib.sha256(f"{salt}:{raw_key}".encode("utf-8")).hexdigest()
    repo = InMemoryRepo(
        {
            token_hash: KeyRecord(
                token=token_hash,
                expires=datetime.now(tz=UTC) + timedelta(seconds=20),
            )
        }
    )
    redis = RecordingRedis()
    service = KeyService(repository=repo, redis_client=redis, salt=salt, auth_cache_ttl_seconds=300)

    await service.validate_key(raw_key)
    cache_key = f"key:v4:{token_hash}"
    assert 1 <= redis.ttls[cache_key] <= 20


@pytest.mark.asyncio
async def test_validate_key_preserves_key_and_team_model_scopes() -> None:
    salt = "test-salt"
    raw_key = "sk-scoped-models"
    token_hash = hashlib.sha256(f"{salt}:{raw_key}".encode("utf-8")).hexdigest()
    repo = InMemoryRepo(
        {
            token_hash: KeyRecord(
                token=token_hash,
                models=["gpt-4o-mini", "text-embedding-3-small"],
                team_models=["gpt-4o-mini", "text-embedding-3-small"],
                expires=datetime.now(tz=UTC) + timedelta(hours=1),
            )
        }
    )
    service = KeyService(repository=repo, salt=salt)

    auth = await service.validate_key(raw_key)

    assert auth.models == ["gpt-4o-mini", "text-embedding-3-small"]
    assert auth.team_models == ["gpt-4o-mini", "text-embedding-3-small"]


@pytest.mark.asyncio
async def test_validate_key_preserves_platform_account_owner_in_auth_cache() -> None:
    salt = "test-salt"
    raw_key = "sk-owned-key"
    token_hash = hashlib.sha256(f"{salt}:{raw_key}".encode("utf-8")).hexdigest()
    repo = InMemoryRepo(
        {
            token_hash: KeyRecord(
                token=token_hash,
                owner_account_id="acct-owner",
                expires=datetime.now(tz=UTC) + timedelta(hours=1),
            )
        }
    )
    redis = RecordingRedis()
    service = KeyService(repository=repo, redis_client=redis, salt=salt)

    first = await service.validate_key(raw_key)
    second = await service.validate_key(raw_key)

    assert first.owner_account_id == "acct-owner"
    assert second.owner_account_id == "acct-owner"
    assert repo.calls == 1


@pytest.mark.asyncio
async def test_validate_key_ignores_pre_owner_contract_cache_entries() -> None:
    salt = "test-salt"
    raw_key = "sk-rotated-cache-contract"
    token_hash = hashlib.sha256(f"{salt}:{raw_key}".encode("utf-8")).hexdigest()
    repo = InMemoryRepo(
        {
            token_hash: KeyRecord(
                token=token_hash,
                owner_account_id="acct-current",
                expires=datetime.now(tz=UTC) + timedelta(hours=1),
            )
        }
    )
    redis = RecordingRedis()
    redis.store[f"key:{token_hash}"] = '{"api_key":"stale-token"}'
    service = KeyService(repository=repo, redis_client=redis, salt=salt)

    auth = await service.validate_key(raw_key)

    assert auth.owner_account_id == "acct-current"
    assert repo.calls == 1
    assert f"key:v4:{token_hash}" in redis.store


@pytest.mark.asyncio
async def test_validate_key_rejects_inactive_organization_before_caching() -> None:
    salt = "test-salt"
    raw_key = "sk-inactive-org"
    token_hash = hashlib.sha256(f"{salt}:{raw_key}".encode("utf-8")).hexdigest()
    repo = InMemoryRepo(
        {
            token_hash: KeyRecord(
                token=token_hash,
                organization_id="org-deleting",
                organization_lifecycle_state="deletion_pending",
            )
        }
    )
    redis = RecordingRedis()
    service = KeyService(repository=repo, redis_client=redis, salt=salt)

    with pytest.raises(Exception, match="Organization is not active"):
        await service.validate_key(raw_key)

    assert redis.store == {}


@pytest.mark.asyncio
async def test_validate_key_preserves_sandbox_budget_and_rate_limit_scope_ids() -> None:
    salt = "test-salt"
    raw_key = "sk-sandbox"
    token_hash = hashlib.sha256(f"{salt}:{raw_key}".encode("utf-8")).hexdigest()
    repo = InMemoryRepo(
        {
            token_hash: KeyRecord(
                token=token_hash,
                user_id="acct-dev",
                team_id="team-sandbox",
                organization_id="org-sandbox",
                max_budget=5.0,
                rpm_limit=1,
                tpm_limit=2,
                key_rph_limit=3,
                key_rpd_limit=4,
                key_tpd_limit=5,
                user_rpm_limit=6,
                user_tpm_limit=7,
                user_rph_limit=8,
                user_rpd_limit=9,
                user_tpd_limit=10,
                team_rpm_limit=11,
                team_tpm_limit=12,
                team_rph_limit=13,
                team_rpd_limit=14,
                team_tpd_limit=15,
                org_rpm_limit=16,
                org_tpm_limit=17,
                org_rph_limit=18,
                org_rpd_limit=19,
                org_tpd_limit=20,
                expires=datetime.now(tz=UTC) + timedelta(hours=1),
            )
        }
    )
    service = KeyService(repository=repo, salt=salt)

    auth = await service.validate_key(raw_key)

    assert auth.user_id == "acct-dev"
    assert auth.team_id == "team-sandbox"
    assert auth.organization_id == "org-sandbox"
    assert auth.max_budget == 5.0
    assert auth.key_rpm_limit == 1
    assert auth.key_tpm_limit == 2
    assert auth.key_rph_limit == 3
    assert auth.key_rpd_limit == 4
    assert auth.key_tpd_limit == 5
    assert auth.user_rpm_limit == 6
    assert auth.user_tpm_limit == 7
    assert auth.user_rph_limit == 8
    assert auth.user_rpd_limit == 9
    assert auth.user_tpd_limit == 10
    assert auth.team_rpm_limit == 11
    assert auth.team_tpm_limit == 12
    assert auth.team_rph_limit == 13
    assert auth.team_rpd_limit == 14
    assert auth.team_tpd_limit == 15
    assert auth.org_rpm_limit == 16
    assert auth.org_tpm_limit == 17
    assert auth.org_rph_limit == 18
    assert auth.org_rpd_limit == 19
    assert auth.org_tpd_limit == 20
