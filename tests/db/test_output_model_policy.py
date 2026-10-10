import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.api.admin.output_policy import schedule_output_policy_invalidation
from src.db.identity.key_repository import KeyRepository
from src.db.identity.output_policy import OutputPolicyChange, persist_output_policy
from src.db.tiers.tiers import TierModelPolicyRecord, TierRepository
from src.services.key_service import KeyService
from tests.conftest import FakeRedis
from tests.db.tier_migration_helpers import (
    cleanup,
    connect_prisma,
    seed_organization,
    seed_tier,
    seed_tier_version,
)

pytestmark = pytest.mark.postgres


async def test_tier_output_create_update_clone_bulk_preserve_revisions():
    db = await connect_prisma()
    identity = uuid4().hex
    repository = TierRepository(db)
    try:
        await seed_tier(db, tier_id=identity, tier_key=f"output-{identity}")
        await seed_tier_version(
            db, tier_id=identity, tier_version_id=identity, version_number=1, status="draft"
        )
        created = await repository.create_model_policy(
            tier_id=identity,
            tier_version_id=identity,
            expected_revision=0,
            policy=TierModelPolicyRecord("", identity, "m", rpm_limit=100, output_tpm_limit=10),
        )
        assert created.policy.output_tpm_limit == 10
        updated = await repository.update_model_policy(
            tier_id=identity,
            tier_version_id=identity,
            tier_model_policy_id=created.policy.tier_model_policy_id,
            expected_revision=1,
            policy=replace(created.policy, output_tpm_limit=20),
        )
        assert updated.policy.output_tpm_limit == 20
        clone = await repository.clone_tier_version(
            tier_id=identity, source_tier_version_id=identity
        )
        cloned = await repository.list_model_policies(tier_version_id=clone.tier_version_id)
        assert cloned[0].output_tpm_limit == 20
        result = await repository.bulk_update_model_policy_limits(
            tier_id=identity,
            tier_version_id=clone.tier_version_id,
            expected_revision=0,
            update_rpm_limit=False,
            rpm_limit=None,
            update_tpm_limit=False,
            tpm_limit=None,
            update_output_tpm_limit=True,
            output_tpm_limit=None,
        )
        assert result.configuration_revision == 1
        cleared = await repository.list_model_policies(tier_version_id=clone.tier_version_id)
        assert cleared[0].output_tpm_limit is None
        assert cleared[0].rpm_limit == 100
    finally:
        await cleanup(db, tier_ids=(identity,))
        await db.disconnect()


@pytest.mark.parametrize("scope", ["team", "key"])
async def test_model_map_auth_projection_clear_and_outbox_are_atomic(scope):
    db = await connect_prisma()
    identity = uuid4().hex
    query = AsyncMock(wraps=db.query_raw)
    service = KeyService(KeyRepository(SimpleNamespace(query_raw=query)), redis_client=FakeRedis())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(key_service=service)))
    try:
        await seed_organization(db, organization_id=identity)
        await db.execute_raw(
            "INSERT INTO deltallm_teamtable (team_id, organization_id, updated_at) VALUES ($1, $1, NOW())",
            identity,
        )
        await db.execute_raw(
            "INSERT INTO deltallm_verificationtoken (id, token, team_id, models, output_tpm_limit, updated_at) VALUES (gen_random_uuid(), $1, $1, ARRAY[]::text[], 100, NOW())",
            identity,
        )
        change = OutputPolicyChange(False, None, True, {"m": 10})
        async with db.tx() as tx:
            await persist_output_policy(tx, scope=scope, identity=identity, change=change)
            await schedule_output_policy_invalidation(
                tx, request=request, scope=scope, identity=identity, change=change
            )
        auth = await service.get_auth_by_token_hash(identity)
        assert query.await_count == 1
        assert getattr(auth, f"{scope}_model_output_tpm_limit") == {"m": 10}
        assert auth.key_output_tpm_limit == 100
        assert getattr(
            await service.get_auth_by_token_hash(identity), f"{scope}_model_output_tpm_limit"
        ) == {"m": 10}
        assert query.await_count == 1
        with pytest.raises(RuntimeError):
            async with db.tx() as tx:
                await persist_output_policy(
                    tx,
                    scope=scope,
                    identity=identity,
                    change=OutputPolicyChange(False, None, True, None),
                )
                raise RuntimeError("Abort mutation")
        await service.invalidate_key_cache_by_hash(identity)
        assert getattr(
            await service.get_auth_by_token_hash(identity), f"{scope}_model_output_tpm_limit"
        ) == {"m": 10}
        async with db.tx() as tx:
            await persist_output_policy(
                tx,
                scope=scope,
                identity=identity,
                change=OutputPolicyChange(False, None, True, None),
            )
            await schedule_output_policy_invalidation(
                tx,
                request=request,
                scope=scope,
                identity=identity,
                change=OutputPolicyChange(False, None, True, None),
            )
        await service.invalidate_key_cache_by_hash(identity)
        assert (
            getattr(
                await service.get_auth_by_token_hash(identity), f"{scope}_model_output_tpm_limit"
            )
            is None
        )
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_cacheinvalidationoutbox WHERE scope_id = $1", identity
        )
        await db.execute_raw("DELETE FROM deltallm_verificationtoken WHERE token = $1", identity)
        await db.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id = $1", identity)
        await cleanup(db, organization_id=identity)
        await db.disconnect()


@pytest.mark.parametrize(
    "value",
    [
        [1],
        {"m": True},
        {"m": "1"},
        {"m": 1.5},
        {"m": 0},
        {"m": 2**31},
        {"*": 1},
        {"m": None},
        {str(i): 1 for i in range(65)},
    ],
)
async def test_database_rejects_invalid_model_maps(value):
    db = await connect_prisma()
    try:
        rows = await db.query_raw(
            "SELECT deltallm_valid_model_output_limits($1::jsonb) AS valid", json.dumps(value)
        )
        assert rows[0]["valid"] is False
    finally:
        await db.disconnect()
