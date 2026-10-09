from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from src.db.repositories import KeyRecord, KeyRepository
from src.metrics.prometheus import get_prometheus_registry
from src.models.errors import AuthenticationError, ServiceUnavailableError
from src.services.key_auth_cache import KeyAuthCache
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
        self.eval_calls: list[tuple[str, int]] = []

    async def get(self, key: str):
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self.store[key] = value
        self.ttls[key] = ttl

    async def eval(self, script, numkeys, *args):
        self.eval_calls.append((script, numkeys))
        if "deltallm_key_auth_revoke_v7" in script:
            key, legacy_key, v4_key, v6_key, payload, legacy_payload, ttl = args
            await self.setex(str(key), int(ttl), str(payload))
            await self.setex(str(legacy_key), int(ttl), str(legacy_payload))
            await self.delete(str(v4_key), str(v6_key))
            return 1
        if "deltallm_key_auth_lookup_v7" in script or "deltallm_key_auth_fill_v7" in script:
            key, legacy_key = map(str, args[:2])
            legacy = self.store.get(legacy_key, "")
            cached = self.store.get(key, "")
            try:
                guard = json.loads(legacy)
            except (ValueError, TypeError):
                guard = None
            if (
                isinstance(guard, dict)
                and guard.get("cache_version") == 5
                and guard.get("cache_kind") == "revoked"
            ):
                cached = legacy
            else:
                try:
                    current = json.loads(cached)
                except (ValueError, TypeError):
                    current = None
                if (
                    isinstance(current, dict)
                    and current.get("cache_version") == 7
                    and current.get("cache_kind") == "allow"
                ):
                    nonce = current.get("cache_guard")
                    if not (
                        isinstance(nonce, str)
                        and nonce
                        and isinstance(guard, dict)
                        and guard.get("cache_version") == 5
                        and guard.get("cache_kind") == "allow"
                        and guard.get("cache_guard") == nonce
                    ):
                        await self.delete(key)
                        cached = ""
            if "deltallm_key_auth_lookup_v7" in script:
                return [cached, int(time.time() * 1000)]
            if cached:
                return cached
            key, legacy_key, payload, legacy_payload, ttl, deadline = args
            if int(time.time() * 1000) > int(deadline):
                return ""
            await self.setex(str(legacy_key), int(ttl), str(legacy_payload))
            await self.setex(str(key), int(ttl), str(payload))
            return str(payload)
        if "deltallm_key_auth_drop_v7" in script:
            keys = [
                key
                for key in args
                if key not in self.store
                or json.loads(self.store[key]).get("cache_kind") != "revoked"
            ]
            await self.delete(*keys)
            return len(keys)
        raise AssertionError("Unsupported key auth cache script")

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


class FailedWriteRedis(RecordingRedis):
    async def eval(self, script: str, numkeys: int, *args: str | int) -> object:
        if "deltallm_key_auth_fill_v7" in script:
            raise RedisConnectionError("Cache write unavailable")
        return await super().eval(script, numkeys, *args)


class InvalidClockRedis(RecordingRedis):
    async def eval(self, script: str, numkeys: int, *args: str | int) -> object:
        del script, numkeys, args
        return ["", "invalid-clock"]


class InvalidFillRedis(RecordingRedis):
    async def eval(self, script: str, numkeys: int, *args: str | int) -> object:
        if "deltallm_key_auth_fill_v7" in script:
            return "invalid-cache-payload"
        return await super().eval(script, numkeys, *args)


@pytest.mark.asyncio
async def test_cache_write_outage_keeps_primary_authorization_without_local_cache() -> None:
    redis = FailedWriteRedis()
    repo = InMemoryRepo({"owned": KeyRecord(token="owned", owner_account_id="owner")})
    service = KeyService(repository=repo, redis_client=redis)
    registry = get_prometheus_registry()
    metric = "deltallm_key_auth_cache_failures_total"
    labels = {"reason": "write_unavailable"}
    before = registry.get_sample_value(metric, labels) or 0

    for _ in range(2):
        auth = await service.get_auth_by_token_hash("owned")
        assert auth.owner_account_id == "owner"

    assert repo.calls == 2 and redis.store == {} and service.primary_gate.active == 0
    assert registry.get_sample_value(metric, labels) == before + 2
    del repo.records["owned"]
    with pytest.raises(AuthenticationError):
        await service.get_auth_by_token_hash("owned")


@pytest.mark.parametrize("bad_clock", [False, True])
@pytest.mark.asyncio
async def test_invalid_cache_lookup_uses_primary_and_still_denies_deleted_key(
    bad_clock: bool,
) -> None:
    redis = InvalidClockRedis() if bad_clock else RecordingRedis()
    redis.store["key:v7:owned"] = "invalid-cache-payload"
    repo = InMemoryRepo({"owned": KeyRecord(token="owned", owner_account_id="owner")})
    service = KeyService(repository=repo, redis_client=redis)

    auth = await service.get_auth_by_token_hash("owned")
    assert auth.owner_account_id == "owner"
    del repo.records["owned"]
    with pytest.raises(AuthenticationError):
        await service.get_auth_by_token_hash("owned")
    assert repo.calls == 2 and service.primary_gate.active == 0


@pytest.mark.asyncio
async def test_revocation_during_primary_read_denies_atomic_cache_fill() -> None:
    redis = RecordingRedis()

    class RevokingRepo(InMemoryRepo):
        async def get_by_token(self, token_hash: str) -> KeyRecord | None:
            record = await super().get_by_token(token_hash)
            await redis.setex(
                "key:v7:" + token_hash,
                62,
                json.dumps({"cache_version": 7, "cache_kind": "revoked"}),
            )
            return record

    repo = RevokingRepo({"owned": KeyRecord(token="owned", owner_account_id="owner")})
    service = KeyService(repository=repo, redis_client=redis)
    with pytest.raises(AuthenticationError):
        await service.get_auth_by_token_hash("owned")
    assert repo.calls == 1 and service.primary_gate.active == 0
    assert json.loads(redis.store["key:v7:owned"])["cache_kind"] == "revoked"


@pytest.mark.asyncio
async def test_invalid_atomic_fill_does_not_allow_stale_primary_snapshot() -> None:
    repo = InMemoryRepo({"owned": KeyRecord(token="owned", owner_account_id="owner")})
    service = KeyService(repository=repo, redis_client=InvalidFillRedis())
    with pytest.raises(ServiceUnavailableError):
        await service.get_auth_by_token_hash("owned")
    assert repo.calls == 1 and service.primary_gate.active == 0


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


@pytest.mark.parametrize("version", [4, 5, 6])
async def test_legacy_allow_cannot_hide_output_policy_and_new_cache_retains_it(version):
    token = "output-policy-upgrade"
    redis = RecordingRedis()
    legacy_auth = {"api_key": token}
    redis.store[f"key:v{version}:{token}"] = json.dumps(
        {"cache_version": 5, "cache_kind": "allow", "auth": legacy_auth}
        if version == 5
        else legacy_auth
    )
    repo = InMemoryRepo(
        {
            token: KeyRecord(
                token=token,
                output_tpm_limit=10,
                user_output_tpm_limit=20,
                team_output_tpm_limit=30,
                org_output_tpm_limit=40,
                model_output_tpm_limit={"model": 50},
                team_model_output_tpm_limit={"model": 60},
            )
        }
    )
    service = KeyService(repository=repo, redis_client=redis)
    for source in ("database", "redis"):
        auth = await service.get_auth_by_token_hash(token)
        assert auth.metadata["auth_cache_source"] == source
        assert (
            auth.key_output_tpm_limit,
            auth.user_output_tpm_limit,
            auth.team_output_tpm_limit,
            auth.org_output_tpm_limit,
        ) == (10, 20, 30, 40)
        assert auth.key_model_output_tpm_limit == {"model": 50}
        assert auth.team_model_output_tpm_limit == {"model": 60}
    assert repo.calls == 1
    assert len(redis.eval_calls) == 3  # Cold lookup + fill, then one cache-hit operation.
    current = json.loads(redis.store[KeyAuthCache.key(token)])
    legacy = json.loads(redis.store[f"key:v5:{token}"])
    assert current["cache_version"] == 7 and legacy["cache_version"] == 5
    assert current["cache_guard"] == legacy["cache_guard"]
    assert not any(name.endswith("_output_tpm_limit") for name in legacy["auth"])


@pytest.mark.parametrize("scope", ["key", "team", "organization", "user"])
async def test_output_policy_invalidation_preserves_both_revocation_namespaces(scope):
    token = "revoked-output-policy"
    redis = RecordingRedis()
    service = KeyService(repository=ScopedRepo([token]), redis_client=redis)
    await service.mark_key_revoked_by_hash(token)
    for version in (4, 6):
        redis.store[f"key:v{version}:{token}"] = "{}"
    invalidate = {
        "key": service.invalidate_key_cache_by_hash,
        "team": service.invalidate_keys_for_team,
        "organization": service.invalidate_keys_for_org,
        "user": service.invalidate_keys_for_user,
    }[scope]
    await invalidate(token)
    assert set(redis.store) == {f"key:v5:{token}", f"key:v7:{token}"}
    for version in (5, 7):
        assert json.loads(redis.store[f"key:v{version}:{token}"]) == {
            "cache_version": version,
            "cache_kind": "revoked",
        }
    with pytest.raises(AuthenticationError):
        await service.get_auth_by_token_hash(token)


@pytest.mark.asyncio
async def test_key_scope_invalidation_batches_redis_deletes() -> None:
    tokens = [f"token-{index}" for index in range(501)]
    repo = ScopedRepo(tokens)
    redis = RecordingRedis()
    service = KeyService(repository=repo, redis_client=redis)

    invalidated = await service.invalidate_keys_for_org("org-1")

    assert invalidated == 501
    assert [len(call) for call in redis.delete_calls] == [500, 500, 500, 3, 500, 1]
    assert set(key for call in redis.delete_calls for key in call) == {
        f"key:v{version}:{token}" for version in (4, 5, 6, 7) for token in tokens
    }


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
    cache_key = f"key:v7:{token_hash}"
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
    cache_key = f"key:v7:{token_hash}"
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
    assert f"key:v7:{token_hash}" in redis.store


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
