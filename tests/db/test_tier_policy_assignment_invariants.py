from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from tests.db.tier_migration_helpers import cleanup
from tests.db.tier_migration_helpers import connect_prisma
from tests.db.tier_migration_helpers import require_tier_schema
from tests.db.tier_migration_helpers import seed_assignment
from tests.db.tier_migration_helpers import seed_capacity_pool
from tests.db.tier_migration_helpers import seed_model_policy
from tests.db.tier_migration_helpers import seed_organization
from tests.db.tier_migration_helpers import seed_tier
from tests.db.tier_migration_helpers import seed_tier_version


@pytest.mark.asyncio
async def test_model_policy_capacity_pool_foreign_key_rejects_missing_pool() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    tier_id = f"tier-policy-pool-{suffix}"
    version_id = f"version-policy-pool-{suffix}"
    callable_key = "openai:gpt-4o"

    try:
        await require_tier_schema(db)
        await seed_tier(db, tier_id=tier_id, tier_key=f"policy-pool-{suffix}")
        await seed_tier_version(
            db,
            tier_version_id=version_id,
            tier_id=tier_id,
            version_number=1,
            status="draft",
        )

        await seed_model_policy(
            db,
            tier_version_id=version_id,
            callable_key="openai:gpt-4o-mini",
            capacity_pool_key=None,
        )

        with pytest.raises(Exception):
            await seed_model_policy(
                db,
                tier_version_id=version_id,
                callable_key=callable_key,
                capacity_pool_key="shared",
            )

        await seed_capacity_pool(
            db,
            tier_version_id=version_id,
            pool_key="shared",
            callable_key=callable_key,
        )
        await seed_model_policy(
            db,
            tier_version_id=version_id,
            callable_key=callable_key,
            capacity_pool_key="shared",
        )

        rows = await db.query_raw(
            """
            SELECT COUNT(*)::int AS total
            FROM deltallm_tiermodelpolicy
            WHERE tier_version_id = $1
            """,
            version_id,
        )
        assert int(rows[0]["total"]) == 2
    finally:
        await cleanup(db, tier_ids=(tier_id,))
        await db.disconnect()


@pytest.mark.asyncio
async def test_capacity_pool_reference_is_deferrable_for_same_key_replacement() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    tier_id = f"tier-pool-replace-{suffix}"
    version_id = f"version-pool-replace-{suffix}"
    callable_key = "anthropic:claude-sonnet"

    try:
        await require_tier_schema(db)
        await seed_tier(db, tier_id=tier_id, tier_key=f"pool-replace-{suffix}")
        await seed_tier_version(
            db,
            tier_version_id=version_id,
            tier_id=tier_id,
            version_number=1,
            status="draft",
        )
        await seed_capacity_pool(
            db,
            tier_version_id=version_id,
            pool_key="reserved",
            callable_key=callable_key,
        )
        await seed_model_policy(
            db,
            tier_version_id=version_id,
            callable_key=callable_key,
            capacity_pool_key="reserved",
        )

        async with db.tx() as tx:
            await tx.execute_raw(
                """
                DELETE FROM deltallm_tiercapacitypool
                WHERE tier_version_id = $1
                  AND pool_key = $2
                  AND callable_key = $3
                """,
                version_id,
                "reserved",
                callable_key,
            )
            await seed_capacity_pool(
                tx,
                tier_version_id=version_id,
                pool_key="reserved",
                callable_key=callable_key,
            )

        rows = await db.query_raw(
            """
            SELECT COUNT(*)::int AS total
            FROM deltallm_tiercapacitypool
            WHERE tier_version_id = $1
              AND pool_key = $2
              AND callable_key = $3
            """,
            version_id,
            "reserved",
            callable_key,
        )
        assert int(rows[0]["total"]) == 1
    finally:
        await cleanup(db, tier_ids=(tier_id,))
        await db.disconnect()


@pytest.mark.asyncio
async def test_primary_assignment_exclusion_rejects_overlapping_enabled_windows() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-primary-overlap-{suffix}"
    tier_id = f"tier-primary-overlap-{suffix}"
    version_id = f"version-primary-overlap-{suffix}"

    try:
        await require_tier_schema(db)
        await seed_organization(db, organization_id=organization_id)
        await seed_tier(db, tier_id=tier_id, tier_key=f"primary-overlap-{suffix}")
        await seed_tier_version(
            db,
            tier_version_id=version_id,
            tier_id=tier_id,
            version_number=1,
            status="active",
        )
        await seed_assignment(
            db,
            assignment_id=f"assignment-primary-base-{suffix}",
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=None,
            starts_at=datetime(2026, 1, 1),
            ends_at=datetime(2026, 2, 1),
        )

        with pytest.raises(Exception):
            await seed_assignment(
                db,
                assignment_id=f"assignment-primary-overlap-{suffix}",
                organization_id=organization_id,
                tier_id=tier_id,
                tier_version_id=None,
                starts_at=datetime(2026, 1, 15),
                ends_at=datetime(2026, 3, 1),
            )

        await seed_assignment(
            db,
            assignment_id=f"assignment-primary-adjacent-{suffix}",
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=None,
            starts_at=datetime(2026, 2, 1),
            ends_at=datetime(2026, 3, 1),
        )
        await seed_assignment(
            db,
            assignment_id=f"assignment-primary-disabled-{suffix}",
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=None,
            enabled=False,
            starts_at=datetime(2026, 1, 15),
            ends_at=datetime(2026, 3, 1),
        )
        await seed_assignment(
            db,
            assignment_id=f"assignment-addon-overlap-{suffix}",
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=None,
            assignment_type="addon",
            starts_at=datetime(2026, 1, 15),
            ends_at=datetime(2026, 3, 1),
        )

        rows = await db.query_raw(
            """
            SELECT COUNT(*)::int AS total
            FROM deltallm_organizationtierassignment
            WHERE organization_id = $1
            """,
            organization_id,
        )
        assert int(rows[0]["total"]) == 4
    finally:
        await cleanup(db, organization_id=organization_id, tier_ids=(tier_id,))
        await db.disconnect()
