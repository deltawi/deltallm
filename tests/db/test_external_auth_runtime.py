from __future__ import annotations

import asyncio
import os
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from prisma import Prisma

from src.api.external_auth import router
from src.auth.external_config import ExternalAuthSettings
from src.bootstrap.external_auth import init_external_auth_runtime
from src.config import AppConfig, GeneralSettings, Settings, resolve_database_settings
from src.db.audit.repository import AuditRepository
from src.services.audit.audit_service import AuditIngestionConfig, AuditService
from src.services.identity.platform_identity_service import PlatformIdentityService
from tests.auth.test_external_assertions import settings_for
from tests.db import external_auth_fixtures as fixtures
from tests.db.test_external_auth_exchange import enable, proof

pytestmark = pytest.mark.postgres
external_database = fixtures.external_database


async def test_owned_runtime_exchange_replay_readiness_and_shutdown(external_database):
    fixture = external_database
    redis_url = os.environ.get("DELTALLM_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("DELTALLM_TEST_REDIS_URL is required")
    redis = Redis.from_url(redis_url, decode_responses=True)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    trust = settings_for(private).model_dump(mode="json")
    trust.update(
        deployment_protocol="external_customer_v1", allowed_origins=["https://console.example.com"]
    )
    trust["integrations"][0]["integration_id"] = fixture.integration_id
    external = ExternalAuthSettings.model_validate(trust)
    config = AppConfig(
        general_settings=GeneralSettings(
            external_auth=external,
            audit_ingestion_mode="outbox",
            audit_ingestion_worker_enabled=True,
            cache_invalidation_worker_enabled=True,
            api_key_auth_cache_ttl_seconds=60,
        )
    )
    app = FastAPI()
    app.state.settings = Settings(database_url=os.environ["DATABASE_URL"])
    app.state.redis = redis
    app.state.platform_identity_service = PlatformIdentityService(
        fixture.db, salt="test-external-salt"
    )
    audit_database = resolve_database_settings(
        AppConfig(general_settings=GeneralSettings(db_pool_size=2)), app.state.settings
    )
    audit_db = Prisma(datasource={"url": audit_database.url})
    await audit_db.connect()
    audit = AuditService(
        AuditRepository(audit_db),
        db_client=audit_db,
        ingestion_config=AuditIngestionConfig(enabled=True),
    )
    app.state.audit_service = audit
    runtime = None
    try:
        await audit.start()
        runtime = await init_external_auth_runtime(app, config)
        assert runtime is not None and await runtime.check_ready()
        assert "connection_limit=4" in str(runtime.db._datasource["url"])
        await enable(fixture)
        app.include_router(router)
        claims = proof(fixture).claims.model_dump()
        assertion = jwt.encode(claims, private, algorithm="RS256", headers={"kid": "rotation-1"})
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://gateway.test"
        ) as client:
            result = await client.post("/auth/external/exchange", json={"assertion": assertion})
            assert result.status_code == 200, result.text
            token = result.json()["session_token"]
            assert token.startswith("psk_ext1_") and "set-cookie" not in result.headers
            replay = await client.post("/auth/external/exchange", json={"assertion": assertion})
            assert replay.status_code == 409
            invalid_correlation = "invalid-" + fixture.integration_id
            invalid = await client.post(
                "/auth/external/exchange",
                json={"assertion": "invalid-secret-assertion"},
                headers={"X-Correlation-ID": invalid_correlation},
            )
            assert invalid.status_code == 401
            denied = await fixture.db.query_raw(
                "SELECT payload_json FROM deltallm_audit_ingestion_outbox WHERE payload_json->'event'->>'correlation_id' = $1",
                invalid_correlation,
            )
            delivered = await fixture.db.query_raw(
                "SELECT metadata FROM deltallm_auditevent WHERE correlation_id = $1",
                invalid_correlation,
            )
            assert denied or delivered
            assert "invalid-secret-assertion" not in str(denied) + str(delivered)
            context = await app.state.platform_identity_service.get_context_for_session(token)
            assert context.external_workspace.binding_id == fixture.binding_id
            runtime.cache_worker_ready = lambda: False
            assert await runtime.check_ready() is False
            unavailable = await client.post(
                "/auth/external/exchange", json={"assertion": assertion}
            )
            assert unavailable.status_code == 503 and runtime.ingress.active == 0
            runtime.cache_worker_ready = lambda: True
            runtime.cleanup_task.cancel()
            await asyncio.gather(runtime.cleanup_task, return_exceptions=True)
            assert await runtime.check_ready() is False
        await runtime.close()
        assert app.state.platform_identity_service.sessions.external is None
        assert not runtime.crypto.ready and not runtime.db.is_connected()
    finally:
        await fixture.db.execute_raw(
            "DELETE FROM deltallm_audit_ingestion_outbox WHERE payload_json->'event'->>'correlation_id' = $1",
            "invalid-" + fixture.integration_id,
        )
        if runtime is not None:
            await runtime.close()
        await audit.shutdown()
        await audit_db.disconnect()
        await redis.aclose()


async def test_durable_revocation_worker_recovers_and_denies_all_auth_paths(external_database):
    from uuid import uuid4
    from redis.exceptions import ConnectionError as RedisConnectionError
    from src.db.runtime.cache_invalidation_outbox import CacheInvalidationOutboxRepository
    from src.db.identity.key_repository import KeyRepository
    from src.services.invalidation.cache_invalidation_worker import CacheInvalidationWorker
    from src.services.identity.keys.key_service import KeyService
    from src.services.identity.keys.key_removal import KeyRemovalService
    from src.models.errors import AuthenticationError
    from tests.db.test_external_customer_keys import own_key
    from tests.db.test_external_auth_exchange import services

    fixture = external_database
    url = os.environ.get("DELTALLM_TEST_REDIS_URL")
    if not url:
        pytest.skip("DELTALLM_TEST_REDIS_URL is required")
    first = Redis.from_url(url, decode_responses=True)
    second = Redis.from_url(url, decode_responses=True)
    exchange, _, _ = services(fixture)
    keys = KeyService(
        KeyRepository(fixture.db), first, salt="test-external-salt", auth_cache_ttl_seconds=60
    )
    replica = KeyService(
        KeyRepository(fixture.writer), second, salt="test-external-salt", auth_cache_ttl_seconds=60
    )
    raw_key = "sk-" + uuid4().hex
    token_hash = await own_key(fixture, raw_key, keys)
    try:
        auth = await replica.validate_key(raw_key)
        for version in (4, 6):
            await second.setex(f"key:v{version}:{token_hash}", 300, auth.model_dump_json())
        original = keys.mark_key_revoked_by_hash

        async def outage(key_hash):
            raise RedisConnectionError("test outage")

        keys.mark_key_revoked_by_hash = outage

        async def approve(db):
            return "self_service"

        removal = await KeyRemovalService(exchange.transactions, exchange.audit, keys).remove(
            token_hash,
            actor_id=fixture.account_id,
            correlation_id="worker-recovery",
            deleted=False,
            approve=approve,
        )
        assert removal.enforcement == "pending"
        keys.mark_key_revoked_by_hash = original
        worker = CacheInvalidationWorker(
            repository=CacheInvalidationOutboxRepository(fixture.db),
            key_service=keys,
            worker_id="external-test-worker",
        )
        await worker.process_once()
        retained = await fixture.db.query_raw(
            "SELECT status, scope_id FROM deltallm_cacheinvalidationoutbox WHERE invalidation_id = $1",
            removal.invalidation_id,
        )
        assert retained[0]["status"] == "completed" and retained[0]["scope_id"] == token_hash
        for version in (4, 6):
            assert await second.get(f"key:v{version}:{token_hash}") is None
        for authenticate in [
            lambda: replica.validate_key(raw_key),
            lambda: replica.get_auth_by_token_hash(token_hash),
        ]:
            with pytest.raises(AuthenticationError):
                await authenticate()
        await replica.invalidate_keys_for_team(fixture.team_id)
        with pytest.raises(AuthenticationError):
            await replica.validate_key(raw_key)
    finally:
        await first.delete(*(f"key:v{version}:{token_hash}" for version in (4, 5, 6, 7)))
        await first.aclose()
        await second.aclose()


async def test_rollback_preview_apply_and_retry_reconcile_all_exact_cache_versions(
    external_database, monkeypatch, capfd
):
    import json
    from uuid import uuid4
    from scripts.external_auth_rollback import main
    from src.db.identity.key_repository import KeyRepository
    from src.models.responses import UserAPIKeyAuth
    from src.services.identity.keys.key_auth_cache import KeyAuthCache
    from src.services.identity.keys.key_removal import KeyRemovalService
    from src.services.identity.keys.key_service import KeyService
    from tests.db.test_external_customer_keys import own_key
    from tests.db.test_external_auth_exchange import services

    fixture = external_database
    url = os.environ.get("DELTALLM_TEST_REDIS_URL")
    if not url:
        pytest.skip("DELTALLM_TEST_REDIS_URL is required")
    monkeypatch.setenv("REDIS_URL", url)
    redis = Redis.from_url(url, decode_responses=True)
    approval = "test-rollback-" + uuid4().hex
    await enable(fixture)
    exchange, _, _ = services(fixture)
    await exchange.exchange(proof(fixture), "rollback-preview")
    keys = KeyService(KeyRepository(fixture.db), salt="test-external-salt")
    token_hash = await own_key(fixture, "sk-" + uuid4().hex, keys)

    async def approve(db):
        return "self_service"

    try:
        removed = await KeyRemovalService(exchange.transactions, exchange.audit, keys).remove(
            token_hash,
            actor_id=fixture.account_id,
            correlation_id="rollback-key-removal",
            deleted=False,
            approve=approve,
        )
        assert removed.enforcement == "pending"
        raw_auth = UserAPIKeyAuth(api_key=token_hash, owner_account_id=fixture.account_id)
        for version in (4, 6):
            await redis.setex(f"key:v{version}:{token_hash}", 300, raw_auth.model_dump_json())
        lookup = await KeyAuthCache(redis).lookup(token_hash)
        await KeyAuthCache(redis).fill(
            token_hash, raw_auth, ttl_seconds=300, deadline_ms=lookup.fill_deadline_ms
        )

        await main(False, None)
        preview = json.loads(capfd.readouterr().out)
        assert preview["applied"] is False and preview["before"] == preview["remaining"]
        assert preview["remaining"]["live_children"] > 0
        assert preview["remaining"]["enabled_integrations"] > 0
        for version in (4, 6):
            assert await redis.ttl(f"key:v{version}:{token_hash}") > 61

        for _ in range(2):
            await main(True, approval)
            result = json.loads(capfd.readouterr().out)
            assert result["reconciled_revocations"] >= 1
            for count in ("live_children", "live_parents", "enabled_integrations"):
                assert result["remaining"][count] == 0
            for version in (4, 6):
                assert await redis.get(f"key:v{version}:{token_hash}") is None
            for version in (5, 7):
                assert json.loads(await redis.get(f"key:v{version}:{token_hash}")) == {
                    "cache_version": version,
                    "cache_kind": "revoked",
                }
        assert await fixture.db.query_raw(
            "SELECT 1 FROM deltallm_audit_ingestion_outbox WHERE payload_json->'event'->'metadata'->>'approval_reference' = $1",
            approval,
        )
    finally:
        await redis.delete(*(f"key:v{version}:{token_hash}" for version in (4, 5, 6, 7)))
        await redis.aclose()
        await fixture.db.execute_raw(
            "DELETE FROM deltallm_audit_ingestion_outbox WHERE payload_json->'event'->'metadata'->>'approval_reference' = $1",
            approval,
        )
