from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.db.tiers import TierRepository
from tests.db.tier_migration_helpers import cleanup
from tests.db.tier_migration_helpers import connect_prisma
from tests.db.tier_migration_helpers import require_tier_schema
from tests.db.tier_migration_helpers import seed_organization
from tests.db.tier_migration_helpers import seed_tier
from tests.db.tier_migration_helpers import seed_tier_version


@pytest.mark.asyncio
async def test_upsert_org_assignment_persists_timestamps_and_metadata_against_postgres() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-assignment-repo-{suffix}"
    tier_id = f"tier-assignment-repo-{suffix}"
    tier_version_id = f"version-assignment-repo-{suffix}"
    starts_at = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    ends_at = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

    try:
        await require_tier_schema(db)
        await seed_organization(db, organization_id=organization_id)
        await seed_tier(db, tier_id=tier_id, tier_key=f"assignment-repo-{suffix}")
        await seed_tier_version(
            db,
            tier_version_id=tier_version_id,
            tier_id=tier_id,
            version_number=1,
            status="active",
        )

        repository = TierRepository(db)
        created = await repository.upsert_org_assignment(
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=tier_version_id,
            assignment_type="addon",
            enabled=False,
            weight=2,
            starts_at=starts_at,
            ends_at=ends_at,
            metadata={"source": "integration", "weights": {"rpm": 100}},
        )

        assert created is not None
        assert created.starts_at == starts_at
        assert created.ends_at == ends_at
        assert created.metadata == {"source": "integration", "weights": {"rpm": 100}}

        updated_ends_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        updated = await repository.upsert_org_assignment(
            assignment_id=created.assignment_id,
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=tier_version_id,
            assignment_type="addon",
            enabled=False,
            weight=3,
            starts_at=starts_at,
            ends_at=updated_ends_at,
            metadata={"source": "updated", "limits": [1, 2, 3]},
        )

        assert updated is not None
        assert updated.assignment_id == created.assignment_id
        assert updated.starts_at == starts_at
        assert updated.ends_at == updated_ends_at
        assert updated.metadata == {"source": "updated", "limits": [1, 2, 3]}
    finally:
        await cleanup(db, organization_id=organization_id, tier_ids=(tier_id,))
        await db.disconnect()
