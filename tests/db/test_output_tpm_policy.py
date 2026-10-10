from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.db.identity.output_policy import OutputPolicyChange, persist_output_policy
from src.config import AppConfig
from src.db.identity.key_repository import KeyRepository
from src.services.key_service import KeyService
from src.services.output_policy_configuration import validate_output_policy_configuration
from tests.db.tier_migration_helpers import connect_prisma, seed_organization
from tests.conftest import FakeRedis
from src.api.admin.output_policy import (
    schedule_output_policy_invalidation,
    invalidate_output_policy_now,
)
from src.db.runtime.cache_invalidation_outbox import CacheInvalidationOutboxRepository
from src.services.cache_invalidation_worker import (
    CacheInvalidationWorker,
    CacheInvalidationWorkerConfig,
)
from fastapi import HTTPException

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("scope", ["key", "user", "team", "organization"])
@pytest.mark.parametrize("value", [100, None], ids=["set", "clear"])
async def test_output_policy_and_invalidation_commit_together_and_worker_recovers(
    scope, value, monkeypatch
):
    db = await connect_prisma()
    identity = f"output-outbox-{uuid4().hex}"
    raw_key = f"sk-{identity}"
    redis = FakeRedis()
    key_service = KeyService(KeyRepository(db), redis_client=redis)
    token = key_service.hash_key(raw_key)
    policy_identity = token if scope == "key" else identity
    field = {
        "key": "key_output_tpm_limit",
        "user": "user_output_tpm_limit",
        "team": "team_output_tpm_limit",
        "organization": "org_output_tpm_limit",
    }[scope]
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(key_service=key_service)))
    try:
        await seed_organization(db, organization_id=identity)
        await db.execute_raw(
            "INSERT INTO deltallm_teamtable (team_id, organization_id, updated_at) VALUES ($1, $1, NOW())",
            identity,
        )
        await db.execute_raw(
            "INSERT INTO deltallm_usertable (user_id, team_id, updated_at) VALUES ($1, $1, NOW())",
            identity,
        )
        await db.execute_raw(
            "INSERT INTO deltallm_verificationtoken (id, token, user_id, team_id, models, updated_at) VALUES (gen_random_uuid(), $1, $2, $2, ARRAY[]::text[], NOW())",
            token,
            identity,
        )
        initial = None if value is not None else 33
        await persist_output_policy(
            db, scope=scope, identity=policy_identity, change=OutputPolicyChange(True, initial)
        )
        original = await key_service.validate_key(raw_key)
        assert getattr(original, field) == initial
        async with db.tx() as tx:
            await persist_output_policy(
                tx, scope=scope, identity=policy_identity, change=OutputPolicyChange(True, value)
            )
            await schedule_output_policy_invalidation(
                tx,
                request=request,
                scope=scope,
                identity=policy_identity,
                change=OutputPolicyChange(True, value),
            )
        method = {
            "key": "invalidate_key_cache_by_hash",
            "user": "invalidate_keys_for_user",
            "team": "invalidate_keys_for_team",
            "organization": "invalidate_keys_for_org",
        }[scope]
        invalidate = getattr(key_service, method)
        monkeypatch.setattr(
            key_service, method, AsyncMock(side_effect=RuntimeError("Token discovery failed"))
        )
        await invalidate_output_policy_now(request, scope=scope, identity=policy_identity)
        pending = await db.query_raw(
            "SELECT status FROM deltallm_cacheinvalidationoutbox WHERE scope_id = $1",
            policy_identity,
        )
        assert [row["status"] for row in pending] == ["pending"]
        assert await redis.get(key_service._cache_key(token)) is not None

        # An enqueue failure rolls the new policy back, rather than committing
        # a mutation with no durable way to refresh authentication.
        with monkeypatch.context() as failed_enqueue:
            failed_enqueue.setattr(
                CacheInvalidationOutboxRepository,
                "enqueue",
                AsyncMock(side_effect=RuntimeError("Outbox unavailable")),
            )
            with pytest.raises(HTTPException, match="could not be scheduled"):
                async with db.tx() as tx:
                    await persist_output_policy(
                        tx,
                        scope=scope,
                        identity=policy_identity,
                        change=OutputPolicyChange(True, 50),
                    )
                    await schedule_output_policy_invalidation(
                        tx,
                        request=request,
                        scope=scope,
                        identity=policy_identity,
                        change=OutputPolicyChange(True, 50),
                    )
        table, column = {
            "key": ("deltallm_verificationtoken", "token"),
            "user": ("deltallm_usertable", "user_id"),
            "team": ("deltallm_teamtable", "team_id"),
            "organization": ("deltallm_organizationtable", "organization_id"),
        }[scope]
        row = await db.query_raw(
            f"SELECT output_tpm_limit FROM {table} WHERE {column} = $1", policy_identity
        )
        assert row[0]["output_tpm_limit"] == value

        monkeypatch.setattr(key_service, method, invalidate)
        await db.execute_raw(
            "UPDATE deltallm_cacheinvalidationoutbox SET next_attempt_at = '1970-01-01', created_at = '1970-01-01' WHERE scope_id = $1",
            policy_identity,
        )
        worker = CacheInvalidationWorker(
            repository=CacheInvalidationOutboxRepository(db),
            key_service=key_service,
            worker_id=identity,
            config=CacheInvalidationWorkerConfig(max_batch_size=1),
        )
        assert await worker.process_once() == 1
        assert await redis.get(key_service._cache_key(token)) is None
        fresh = await key_service.validate_key(raw_key)
        assert getattr(fresh, field) == value
        completed = await db.query_raw(
            "SELECT status FROM deltallm_cacheinvalidationoutbox WHERE scope_id = $1",
            policy_identity,
        )
        assert [row["status"] for row in completed] == ["completed"]
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_cacheinvalidationoutbox WHERE scope_id = $1", policy_identity
        )
        await db.execute_raw("DELETE FROM deltallm_verificationtoken WHERE token = $1", token)
        await db.execute_raw("DELETE FROM deltallm_usertable WHERE user_id = $1", identity)
        await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", identity)
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1", identity
        )
        await db.disconnect()


async def test_output_policies_round_trip_in_joined_auth_query_and_preserve_omission():
    db = await connect_prisma()
    identity = f"output-{uuid4().hex}"
    try:
        await seed_organization(db, organization_id=identity)
        await db.execute_raw(
            "INSERT INTO deltallm_teamtable (team_id, organization_id, updated_at) VALUES ($1, $1, NOW())",
            identity,
        )
        await db.execute_raw(
            "INSERT INTO deltallm_usertable (user_id, team_id, updated_at) VALUES ($1, $1, NOW())",
            identity,
        )
        await db.execute_raw(
            "INSERT INTO deltallm_verificationtoken (id, token, user_id, team_id, models, updated_at) VALUES (gen_random_uuid(), $1, $1, $1, ARRAY[]::text[], NOW())",
            identity,
        )
        for index, scope in enumerate(("organization", "team", "user", "key")):
            await persist_output_policy(
                db, scope=scope, identity=identity, change=OutputPolicyChange(True, 100 + index)
            )
            await persist_output_policy(
                db, scope=scope, identity=identity, change=OutputPolicyChange(False, None)
            )
        query = AsyncMock(wraps=db.query_raw)
        record = await KeyRepository(SimpleNamespace(query_raw=query)).get_by_token(identity)
        assert query.await_count == 1
        sql, *params = query.call_args.args
        plan = await db.query_raw("EXPLAIN (FORMAT JSON) " + sql, *params)
        assert plan
        assert record is not None
        auth = KeyService(KeyRepository(db))._auth_from_record(record)
        assert (
            auth.org_output_tpm_limit,
            auth.team_output_tpm_limit,
            auth.user_output_tpm_limit,
            auth.key_output_tpm_limit,
        ) == (100, 101, 102, 103)
        for scope in ("organization", "team", "user", "key"):
            await persist_output_policy(
                db, scope=scope, identity=identity, change=OutputPolicyChange(True, None)
            )
        cleared = await KeyRepository(db).get_by_token(identity)
        assert cleared is not None and cleared.output_tpm_limit is None
    finally:
        await db.execute_raw("DELETE FROM deltallm_verificationtoken WHERE token = $1", identity)
        await db.execute_raw("DELETE FROM deltallm_usertable WHERE user_id = $1", identity)
        await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", identity)
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1", identity
        )
        await db.disconnect()


@pytest.mark.parametrize("scope", ["team", "organization"])
@pytest.mark.parametrize("recover", [False, True], ids=["immediate", "worker"])
async def test_service_account_scope_invalidation_matches_auth_precedence(scope, recover):
    db = await connect_prisma()
    identity = f"output-service-{uuid4().hex}"
    other = f"{identity}-other"
    redis = FakeRedis()
    query = AsyncMock(wraps=db.query_raw)
    service = KeyService(KeyRepository(SimpleNamespace(query_raw=query)), redis_client=redis)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(key_service=service)))
    # Direct key team, runtime-user team, then service-account team have auth precedence.
    cases = [
        ("service", None, None, identity, True),
        ("user", None, identity, other, True),
        ("direct", identity, None, other, True),
        ("override", other, None, identity, False),
        ("unrelated", None, None, other, False),
    ]
    try:
        for owner in (identity, other):
            await seed_organization(db, organization_id=owner)
            await db.execute_raw(
                "INSERT INTO deltallm_teamtable (team_id, organization_id, updated_at) VALUES ($1, $1, NOW())",
                owner,
            )
            await db.execute_raw(
                "INSERT INTO deltallm_usertable (user_id, team_id, updated_at) VALUES ($1, $1, NOW())",
                owner,
            )
            await db.execute_raw(
                "INSERT INTO deltallm_serviceaccount (service_account_id, team_id, name, updated_at) VALUES ($1, $1, 'output-test', NOW())",
                owner,
            )
        for label, team, user, account, affected in cases:
            token = f"{identity}-{label}"
            await db.execute_raw(
                "INSERT INTO deltallm_verificationtoken (id, token, team_id, user_id, owner_service_account_id, models, updated_at) VALUES (gen_random_uuid(), $1, $2, $3, $4, ARRAY[]::text[], NOW())",
                token,
                team,
                user,
                account,
            )
            auth = await service.get_auth_by_token_hash(token)
            assert auth.team_id == (identity if affected else other)
            assert auth.organization_id == auth.team_id
            await redis.setex(f"key:v4:{token}", 300, "legacy")

        async with db.tx() as tx:
            await persist_output_policy(
                tx, scope=scope, identity=identity, change=OutputPolicyChange(True, 100)
            )
            if recover:
                await schedule_output_policy_invalidation(
                    tx,
                    request=request,
                    scope=scope,
                    identity=identity,
                    change=OutputPolicyChange(True, 100),
                )
        query.reset_mock()
        if recover:
            await db.execute_raw(
                "UPDATE deltallm_cacheinvalidationoutbox SET next_attempt_at = '1970-01-01', created_at = '1970-01-01' WHERE scope_id = $1",
                identity,
            )
            worker = CacheInvalidationWorker(
                repository=CacheInvalidationOutboxRepository(db),
                key_service=service,
                worker_id=identity,
                config=CacheInvalidationWorkerConfig(max_batch_size=1),
            )
            assert await worker.process_once() == 1
            rows = await db.query_raw(
                "SELECT status FROM deltallm_cacheinvalidationoutbox WHERE scope_id = $1", identity
            )
            assert [row["status"] for row in rows] == ["completed"]
        else:
            await invalidate_output_policy_now(request, scope=scope, identity=identity)
        assert query.await_count == 1
        sql, *params = query.call_args.args
        assert await db.query_raw("EXPLAIN (FORMAT JSON) " + sql, *params)
        for label, _, _, _, affected in cases:
            token = f"{identity}-{label}"
            assert (await redis.get(service._cache_key(token)) is None) == affected
            assert (await redis.get(f"key:v4:{token}") is None) == affected
            fresh = await service.get_auth_by_token_hash(token)
            field = "team_output_tpm_limit" if scope == "team" else "org_output_tpm_limit"
            assert getattr(fresh, field) == (100 if affected else None)
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_cacheinvalidationoutbox WHERE scope_id = $1", identity
        )
        for label, _, _, _, _ in cases:
            await db.execute_raw(
                "DELETE FROM deltallm_verificationtoken WHERE token = $1", f"{identity}-{label}"
            )
        for owner in (identity, other):
            await db.execute_raw(
                "DELETE FROM deltallm_serviceaccount WHERE service_account_id = $1", owner
            )
            await db.execute_raw("DELETE FROM deltallm_usertable WHERE user_id = $1", owner)
            await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", owner)
            await db.execute_raw(
                "DELETE FROM deltallm_organizationtable WHERE organization_id = $1", owner
            )
        await db.disconnect()


async def test_database_invariant_and_startup_auth_compatibility():
    db = await connect_prisma()
    identity = f"output-{uuid4().hex}"
    try:
        await seed_organization(db, organization_id=identity)
        with pytest.raises(Exception, match="output_tpm_positive"):
            await persist_output_policy(
                db, scope="organization", identity=identity, change=OutputPolicyChange(True, 0)
            )
        await persist_output_policy(
            db, scope="organization", identity=identity, change=OutputPolicyChange(True, 1)
        )
        config = AppConfig.model_validate(
            {"general_settings": {"redis_degraded_mode": "fail_closed", "enable_jwt_auth": True}}
        )
        with pytest.raises(ValueError, match="stored API-key"):
            await validate_output_policy_configuration(
                db, config, redis_available=True, degraded_mode="fail_closed"
            )
        config.general_settings.enable_jwt_auth = False
        with pytest.raises(ValueError, match="fail_closed"):
            await validate_output_policy_configuration(
                db, config, redis_available=False, degraded_mode="fail_closed"
            )
        await validate_output_policy_configuration(
            db, config, redis_available=True, degraded_mode="fail_closed"
        )
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1", identity
        )
        await db.disconnect()
