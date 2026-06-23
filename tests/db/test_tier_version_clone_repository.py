from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

from src.db.tier_records import parse_json_object
from src.db.tiers import TierRepository
from tests.db.tier_migration_helpers import cleanup
from tests.db.tier_migration_helpers import connect_prisma
from tests.db.tier_migration_helpers import require_tier_schema
from tests.db.tier_migration_helpers import seed_tier


@pytest.mark.asyncio
async def test_clone_tier_version_copies_pool_and_policy_rows_against_postgres() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    tier_id = f"tier-clone-repo-{suffix}"
    source_version_id = f"version-clone-repo-{suffix}"
    source_pool_id = f"pool-clone-repo-{suffix}"
    source_policy_id = f"policy-clone-repo-{suffix}"
    schema_available = False

    try:
        await require_tier_schema(db)
        schema_available = True
        await seed_tier(db, tier_id=tier_id, tier_key=f"clone-repo-{suffix}")
        await _seed_source_tier_version(
            db,
            tier_id=tier_id,
            tier_version_id=source_version_id,
        )
        await _seed_capacity_pool(
            db,
            tier_capacity_pool_id=source_pool_id,
            tier_version_id=source_version_id,
        )
        await _seed_model_policy(
            db,
            tier_model_policy_id=source_policy_id,
            tier_version_id=source_version_id,
        )

        cloned = await TierRepository(db).clone_tier_version(
            tier_id=tier_id,
            source_tier_version_id=source_version_id,
        )

        assert cloned is not None
        assert cloned.tier_id == tier_id
        assert cloned.version_number == 8
        assert cloned.status == "draft"
        assert cloned.published_at is None
        assert cloned.published_by_account_id is None
        assert cloned.metadata == {"copied_from": "source", "version": 7}
        assert cloned.capacity_pool_count == 1
        assert cloned.model_policy_count == 1
        assert cloned.assignment_count == 0

        pools = await db.query_raw(
            """
            SELECT
                tier_capacity_pool_id,
                tier_version_id,
                pool_key,
                callable_key,
                rpm_capacity,
                tpm_capacity,
                max_parallel_requests,
                strategy,
                saturation_threshold,
                burst_multiplier,
                metadata
            FROM deltallm_tiercapacitypool
            WHERE tier_version_id = $1
            """,
            cloned.tier_version_id,
        )
        assert len(pools) == 1
        assert pools[0]["tier_capacity_pool_id"] != source_pool_id
        assert pools[0]["tier_version_id"] == cloned.tier_version_id
        assert pools[0]["pool_key"] == "shared-chat"
        assert pools[0]["callable_key"] == "openai:gpt-4o-mini"
        assert int(pools[0]["rpm_capacity"]) == 1000
        assert int(pools[0]["tpm_capacity"]) == 250000
        assert int(pools[0]["max_parallel_requests"]) == 12
        assert pools[0]["strategy"] == "weighted_fair"
        assert float(pools[0]["saturation_threshold"]) == pytest.approx(0.85)
        assert float(pools[0]["burst_multiplier"]) == pytest.approx(1.5)
        assert parse_json_object(pools[0]["metadata"]) == {"pool": "shared"}

        policies = await db.query_raw(
            """
            SELECT
                tier_model_policy_id,
                tier_version_id,
                callable_key,
                enabled,
                access_mode,
                rpm_limit,
                tpm_limit,
                max_parallel_requests,
                batch_rpm_limit,
                batch_tpm_limit,
                pricing,
                capacity_pool_key,
                priority,
                metadata
            FROM deltallm_tiermodelpolicy
            WHERE tier_version_id = $1
            """,
            cloned.tier_version_id,
        )
        assert len(policies) == 1
        assert policies[0]["tier_model_policy_id"] != source_policy_id
        assert policies[0]["tier_version_id"] == cloned.tier_version_id
        assert policies[0]["callable_key"] == "openai:gpt-4o-mini"
        assert policies[0]["enabled"] is True
        assert policies[0]["access_mode"] == "allow"
        assert int(policies[0]["rpm_limit"]) == 500
        assert int(policies[0]["tpm_limit"]) == 100000
        assert int(policies[0]["max_parallel_requests"]) == 8
        assert int(policies[0]["batch_rpm_limit"]) == 100
        assert int(policies[0]["batch_tpm_limit"]) == 50000
        assert policies[0]["capacity_pool_key"] == "shared-chat"
        assert int(policies[0]["priority"]) == 10
        assert parse_json_object(policies[0]["pricing"]) == {
            "input_cost_per_token": 0.01,
            "output_cost_per_token": 0.02,
        }
        assert parse_json_object(policies[0]["metadata"]) == {"policy": "chat"}
    finally:
        if schema_available:
            await cleanup(db, tier_ids=(tier_id,))
        await db.disconnect()


async def _seed_source_tier_version(
    db: Any,
    *,
    tier_id: str,
    tier_version_id: str,
) -> None:
    await db.execute_raw(
        """
        INSERT INTO deltallm_tierversion (
            tier_version_id,
            tier_id,
            version_number,
            status,
            metadata,
            created_at,
            updated_at
        )
        VALUES ($1, $2, 7, 'active', $3::jsonb, NOW(), NOW())
        """,
        tier_version_id,
        tier_id,
        json.dumps({"copied_from": "source", "version": 7}),
    )


async def _seed_capacity_pool(
    db: Any,
    *,
    tier_capacity_pool_id: str,
    tier_version_id: str,
) -> None:
    await db.execute_raw(
        """
        INSERT INTO deltallm_tiercapacitypool (
            tier_capacity_pool_id,
            tier_version_id,
            pool_key,
            callable_key,
            rpm_capacity,
            tpm_capacity,
            max_parallel_requests,
            strategy,
            saturation_threshold,
            burst_multiplier,
            metadata,
            created_at,
            updated_at
        )
        VALUES (
            $1,
            $2,
            'shared-chat',
            'openai:gpt-4o-mini',
            1000,
            250000,
            12,
            'weighted_fair',
            0.85,
            1.5,
            $3::jsonb,
            NOW(),
            NOW()
        )
        """,
        tier_capacity_pool_id,
        tier_version_id,
        json.dumps({"pool": "shared"}),
    )


async def _seed_model_policy(
    db: Any,
    *,
    tier_model_policy_id: str,
    tier_version_id: str,
) -> None:
    await db.execute_raw(
        """
        INSERT INTO deltallm_tiermodelpolicy (
            tier_model_policy_id,
            tier_version_id,
            callable_key,
            enabled,
            access_mode,
            rpm_limit,
            tpm_limit,
            max_parallel_requests,
            batch_rpm_limit,
            batch_tpm_limit,
            pricing,
            capacity_pool_key,
            priority,
            metadata,
            created_at,
            updated_at
        )
        VALUES (
            $1,
            $2,
            'openai:gpt-4o-mini',
            TRUE,
            'allow',
            500,
            100000,
            8,
            100,
            50000,
            $3::jsonb,
            'shared-chat',
            10,
            $4::jsonb,
            NOW(),
            NOW()
        )
        """,
        tier_model_policy_id,
        tier_version_id,
        json.dumps(
            {
                "input_cost_per_token": 0.01,
                "output_cost_per_token": 0.02,
            }
        ),
        json.dumps({"policy": "chat"}),
    )
