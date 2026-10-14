from __future__ import annotations

from uuid import uuid4

import pytest

from src.db.tiers.tiers import TierRepository
from tests.db.tier_migration_helpers import cleanup
from tests.db.tier_migration_helpers import connect_prisma
from tests.db.tier_migration_helpers import require_tier_schema


pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_create_tier_with_initial_draft_creates_and_replays_against_postgres() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    principal_scope = f"test-bootstrap:{suffix}"
    idempotency_key = f"key-{suffix}"
    created_tier_id: str | None = None
    schema_available = False

    async def bootstrap():
        return await TierRepository(db).create_tier_with_initial_draft(
            principal_scope=principal_scope,
            idempotency_key=idempotency_key,
            request_hash="a" * 64,
            tier_key=f"bootstrap-{suffix}",
            name="Bootstrap Tier",
            description=None,
            enabled=True,
            metadata=None,
            created_by_account_id=None,
            created_by_kind="master_key",
        )

    try:
        await require_tier_schema(db)
        schema_available = True

        # Runs the advisory lock through query_raw, which must return a
        # deserializable column (pg_advisory_xact_lock itself returns void).
        created = await bootstrap()
        created_tier_id = created.tier.tier_id

        assert created.idempotency_resolution == "created"
        assert created.tier.tier_key == f"bootstrap-{suffix}"
        assert created.initial_version.tier_id == created_tier_id
        assert created.initial_version.version_number == 1
        assert created.initial_version.status == "draft"
        assert created.initial_version.created_by_kind == "master_key"

        replayed = await bootstrap()

        assert replayed.idempotency_resolution == "replayed"
        assert replayed.tier.tier_id == created_tier_id
        assert replayed.initial_version.tier_version_id == created.initial_version.tier_version_id
    finally:
        if schema_available:
            await db.execute_raw(
                "DELETE FROM deltallm_tiercreationrequest WHERE principal_scope = $1",
                principal_scope,
            )
            if created_tier_id is not None:
                await cleanup(db, tier_ids=(created_tier_id,))
        await db.disconnect()
