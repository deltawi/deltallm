from __future__ import annotations

from uuid import uuid4

import pytest
from redis.exceptions import ConnectionError

from src.audit.actions import AuditAction
from src.auth.external_errors import ExternalAuthError, ExternalAuthUnavailable
from src.db.identity.key_repository import KeyRepository
from src.services.external_inference_keys import ExternalInferenceKeyService
from src.services.key_removal import KeyRemovalService
from src.services.key_service import KeyService
from tests.db import external_auth_fixtures as fixtures
from tests.db.test_external_auth_exchange import enable, proof, services

pytestmark = pytest.mark.postgres
external_database = fixtures.external_database


class Cache:
    def __init__(self, *, unavailable=False):
        self.unavailable = unavailable
        self.markers = {}

    async def eval(self, script, numkeys, *args):
        assert "deltallm_key_auth_revoke_v7" in script and numkeys == 4
        key, legacy_key, v4_key, v6_key, payload, legacy_payload, ttl = args
        await self.setex(key, ttl, payload)
        await self.setex(legacy_key, ttl, legacy_payload)
        self.markers.pop(v4_key, None)
        self.markers.pop(v6_key, None)
        return 1

    async def setex(self, key, ttl, payload):
        if self.unavailable:
            raise ConnectionError("test outage")
        self.markers[key] = (ttl, payload)


async def own_key(fixture, raw_key, keys):
    token_hash = keys.hash_key(raw_key)
    await fixture.db.execute_raw(
        "INSERT INTO deltallm_verificationtoken (id, token, key_name, owner_account_id, user_id, team_id, models, updated_at) VALUES (gen_random_uuid(), $1, 'own-key', $2, $2, $3, ARRAY[]::text[], NOW())",
        token_hash,
        fixture.account_id,
        fixture.team_id,
    )
    return token_hash


async def test_inference_key_selection_requires_owner_runtime_team_and_ready_session(
    external_database,
):
    fixture = external_database
    await enable(fixture)
    exchange, sessions, _ = services(fixture)
    response = await exchange.exchange(proof(fixture), "first")
    context = await sessions.get_context(
        exchange.identities.sessions.hash_token(response.session_token)
    )
    keys = KeyService(KeyRepository(fixture.db), salt="test-external-salt")
    raw_key = "sk-" + uuid4().hex
    token_hash = await own_key(fixture, raw_key, keys)
    service = ExternalInferenceKeyService(exchange.transactions, keys.salt)
    selected = await service.select(raw_key, context)
    assert selected.account_id == fixture.account_id and selected.team_id == fixture.team_id
    assert (
        raw_key not in selected.model_dump_json() and token_hash not in selected.model_dump_json()
    )
    context.force_password_change = True
    with pytest.raises(ExternalAuthError):
        await service.select(raw_key, context)
    context.force_password_change = False
    for field in ("owner_account_id", "user_id", "team_id"):
        await fixture.writer.execute_raw(
            f"UPDATE deltallm_verificationtoken SET {field} = NULL WHERE token = $1", token_hash
        )
        with pytest.raises(ExternalAuthError):
            await service.select(raw_key, context)
        original = fixture.team_id if field == "team_id" else fixture.account_id
        await fixture.writer.execute_raw(
            f"UPDATE deltallm_verificationtoken SET {field} = $2 WHERE token = $1",
            token_hash,
            original,
        )


@pytest.mark.parametrize("unavailable", [False, True])
async def test_key_removal_commits_required_audit_and_exact_hash_outbox_before_marker(
    external_database, unavailable
):
    fixture = external_database
    await enable(fixture)
    exchange, _, _ = services(fixture)
    cache = Cache(unavailable=unavailable)
    keys = KeyService(
        KeyRepository(fixture.db), cache, salt="test-external-salt", auth_cache_ttl_seconds=60
    )
    token_hash = await own_key(fixture, "sk-" + uuid4().hex, keys)

    async def approve(db):
        return "self_service"

    result = await KeyRemovalService(exchange.transactions, exchange.audit, keys).remove(
        token_hash,
        actor_id=fixture.account_id,
        correlation_id="remove",
        deleted=False,
        approve=approve,
    )
    assert result.removed and result.enforcement == ("pending" if unavailable else "enforced")
    assert not await fixture.db.query_raw(
        "SELECT 1 FROM deltallm_verificationtoken WHERE token = $1", token_hash
    )
    retained = await fixture.db.query_raw(
        "SELECT * FROM deltallm_cacheinvalidationoutbox WHERE invalidation_id = $1",
        result.invalidation_id,
    )
    assert (
        retained[0]["scope_id"] == token_hash and retained[0]["metadata"]["auth_revocation"] is True
    )
    audited = await fixture.db.query_raw(
        "SELECT payload_json FROM deltallm_audit_ingestion_outbox WHERE payload_json->'event'->>'correlation_id' = 'remove' AND payload_json->'event'->>'actor_id' = $1",
        fixture.account_id,
    )
    assert audited[0]["payload_json"]["event"]["resource_id"] == token_hash
    assert audited[0]["payload_json"]["event"]["action"] == AuditAction.ADMIN_KEY_SELF_REVOKE.value
    if not unavailable:
        assert cache.markers[KeyService._cache_key(token_hash)][0] == 62
        assert cache.markers[f"key:v5:{token_hash}"][0] == 62


async def test_key_removal_audit_failure_rolls_back_key_and_outbox(external_database, monkeypatch):
    fixture = external_database
    await enable(fixture)
    exchange, _, _ = services(fixture)
    cache = Cache()
    keys = KeyService(
        KeyRepository(fixture.db), cache, salt="test-external-salt", auth_cache_ttl_seconds=60
    )
    token_hash = await own_key(fixture, "sk-" + uuid4().hex, keys)

    async def approve(db):
        return "self_service"

    async def failure(db, event):
        raise ExternalAuthUnavailable()

    monkeypatch.setattr(exchange.audit, "write", failure)
    with pytest.raises(ExternalAuthUnavailable):
        await KeyRemovalService(exchange.transactions, exchange.audit, keys).remove(
            token_hash,
            actor_id=fixture.account_id,
            correlation_id="remove",
            deleted=True,
            approve=approve,
        )
    assert await fixture.db.query_raw(
        "SELECT 1 FROM deltallm_verificationtoken WHERE token = $1", token_hash
    )
    assert not await fixture.db.query_raw(
        "SELECT 1 FROM deltallm_cacheinvalidationoutbox WHERE scope_id = $1", token_hash
    )
    assert not cache.markers


async def test_revocation_status_is_private_and_bounded(external_database):
    from src.services.key_revocation_status import KeyRevocationStatusService

    fixture = external_database
    await enable(fixture)
    exchange, _, _ = services(fixture)
    keys = KeyService(
        KeyRepository(fixture.db),
        Cache(unavailable=True),
        salt="test-external-salt",
        auth_cache_ttl_seconds=60,
    )
    token_hash = await own_key(fixture, "sk-" + uuid4().hex, keys)

    async def approve(db):
        return "self_service"

    removal = await KeyRemovalService(exchange.transactions, exchange.audit, keys).remove(
        token_hash,
        actor_id=fixture.account_id,
        correlation_id="status",
        deleted=False,
        approve=approve,
    )
    status = KeyRevocationStatusService(exchange.transactions, 60)
    pending = await status.read(removal.invalidation_id, fixture.account_id)
    assert pending.enforcement == "pending" and token_hash not in pending.model_dump_json()
    with pytest.raises(ExternalAuthError):
        await status.read(removal.invalidation_id, "foreign-account")
    await fixture.db.execute_raw(
        "UPDATE deltallm_cacheinvalidationoutbox SET created_at = (clock_timestamp() AT TIME ZONE 'UTC') - INTERVAL '62 seconds' WHERE invalidation_id = $1",
        removal.invalidation_id,
    )
    assert (
        await status.read(removal.invalidation_id, fixture.account_id)
    ).enforcement == "enforced"
