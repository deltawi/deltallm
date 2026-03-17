from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from src.db.repositories import KeyRecord
from src.services.key_service import KeyService


class InMemoryRepo:
    def __init__(self, records: dict[str, KeyRecord]) -> None:
        self.records = records
        self.calls = 0

    async def get_by_token(self, token_hash: str) -> KeyRecord | None:
        self.calls += 1
        return self.records.get(token_hash)


class RecordingRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self.store[key] = value
        self.ttls[key] = ttl

    async def delete(self, *keys: str):
        for key in keys:
            self.store.pop(key, None)
            self.ttls.pop(key, None)


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
    cache_key = f"key:{token_hash}"
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
    cache_key = f"key:{token_hash}"
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
