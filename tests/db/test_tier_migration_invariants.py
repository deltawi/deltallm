from __future__ import annotations

from uuid import uuid4

import pytest

from tests.db.tier_migration_helpers import cleanup as _cleanup
from tests.db.tier_migration_helpers import connect_prisma as _connect_prisma
from tests.db.tier_migration_helpers import require_tier_schema as _require_tier_schema
from tests.db.tier_migration_helpers import seed_assignment as _seed_assignment
from tests.db.tier_migration_helpers import seed_organization as _seed_organization
from tests.db.tier_migration_helpers import seed_tier as _seed_tier
from tests.db.tier_migration_helpers import seed_tier_version as _seed_tier_version


@pytest.mark.asyncio
async def test_tier_version_creation_constraints_reject_invalid_rows() -> None:
    db = await _connect_prisma()
    suffix = uuid4().hex
    tier_id = f"tier-invariants-{suffix}"

    try:
        await _require_tier_schema(db)
        await _seed_tier(db, tier_id=tier_id, tier_key=f"invariants-{suffix}")

        with pytest.raises(Exception):
            await db.execute_raw(
                """
                INSERT INTO deltallm_tierversion (
                    tier_version_id,
                    tier_id,
                    version_number,
                    status,
                    created_at,
                    updated_at
                )
                VALUES ($1, $2, 0, 'draft', NOW(), NOW())
                """,
                f"version-zero-{suffix}",
                tier_id,
            )

        with pytest.raises(Exception):
            await db.execute_raw(
                """
                INSERT INTO deltallm_tierversion (
                    tier_version_id,
                    tier_id,
                    version_number,
                    status,
                    published_at,
                    created_at,
                    updated_at
                )
                VALUES ($1, $2, 1, 'draft', NOW(), NOW(), NOW())
                """,
                f"version-published-draft-{suffix}",
                tier_id,
            )

        rows = await db.query_raw(
            """
            SELECT COUNT(*)::int AS total
            FROM deltallm_tierversion
            WHERE tier_id = $1
            """,
            tier_id,
        )
        assert int(rows[0]["total"]) == 0
    finally:
        await _cleanup(db, tier_ids=(tier_id,))
        await db.disconnect()


@pytest.mark.asyncio
async def test_active_tier_version_unique_index_rejects_second_active_version() -> None:
    db = await _connect_prisma()
    suffix = uuid4().hex
    tier_id = f"tier-active-{suffix}"
    active_version_id = f"version-active-{suffix}"
    draft_version_id = f"version-draft-{suffix}"

    try:
        await _require_tier_schema(db)
        await _seed_tier(db, tier_id=tier_id, tier_key=f"active-{suffix}")
        await _seed_tier_version(
            db,
            tier_version_id=active_version_id,
            tier_id=tier_id,
            version_number=1,
            status="active",
        )
        await _seed_tier_version(
            db,
            tier_version_id=draft_version_id,
            tier_id=tier_id,
            version_number=2,
            status="draft",
        )

        with pytest.raises(Exception):
            await db.execute_raw(
                """
                UPDATE deltallm_tierversion
                SET status = 'active',
                    updated_at = NOW()
                WHERE tier_version_id = $1
                """,
                draft_version_id,
            )

        rows = await db.query_raw(
            """
            SELECT status
            FROM deltallm_tierversion
            WHERE tier_id = $1
            ORDER BY version_number ASC
            """,
            tier_id,
        )
        assert [row["status"] for row in rows] == ["active", "draft"]
    finally:
        await _cleanup(db, tier_ids=(tier_id,))
        await db.disconnect()


@pytest.mark.asyncio
async def test_assignment_version_foreign_key_rejects_cross_tier_version() -> None:
    db = await _connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-invariants-{suffix}"
    source_tier_id = f"tier-source-{suffix}"
    target_tier_id = f"tier-target-{suffix}"
    source_version_id = f"version-source-{suffix}"

    try:
        await _require_tier_schema(db)
        await _seed_organization(db, organization_id=organization_id)
        await _seed_tier(db, tier_id=source_tier_id, tier_key=f"source-{suffix}")
        await _seed_tier(db, tier_id=target_tier_id, tier_key=f"target-{suffix}")
        await _seed_tier_version(
            db,
            tier_version_id=source_version_id,
            tier_id=source_tier_id,
            version_number=1,
            status="active",
        )

        with pytest.raises(Exception):
            await db.execute_raw(
                """
                INSERT INTO deltallm_organizationtierassignment (
                    assignment_id,
                    organization_id,
                    tier_id,
                    tier_version_id,
                    assignment_type,
                    enabled,
                    weight,
                    created_at,
                    updated_at
                )
                VALUES ($1, $2, $3, $4, 'primary', TRUE, 1, NOW(), NOW())
                """,
                f"assignment-cross-tier-{suffix}",
                organization_id,
                target_tier_id,
                source_version_id,
            )

        rows = await db.query_raw(
            """
            SELECT COUNT(*)::int AS total
            FROM deltallm_organizationtierassignment
            WHERE organization_id = $1
            """,
            organization_id,
        )
        assert int(rows[0]["total"]) == 0
    finally:
        await _cleanup(
            db,
            organization_id=organization_id,
            tier_ids=(source_tier_id, target_tier_id),
        )
        await db.disconnect()


@pytest.mark.asyncio
async def test_assignment_active_version_trigger_rejects_non_active_versions() -> None:
    db = await _connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-active-assignment-{suffix}"
    tier_id = f"tier-active-assignment-{suffix}"
    active_version_id = f"version-active-assignment-{suffix}"
    draft_version_id = f"version-draft-assignment-{suffix}"
    archived_version_id = f"version-archived-assignment-{suffix}"

    try:
        await _require_tier_schema(db)
        await _seed_organization(db, organization_id=organization_id)
        await _seed_tier(db, tier_id=tier_id, tier_key=f"assignable-{suffix}")
        await _seed_tier_version(
            db,
            tier_version_id=active_version_id,
            tier_id=tier_id,
            version_number=1,
            status="active",
        )
        await _seed_tier_version(
            db,
            tier_version_id=draft_version_id,
            tier_id=tier_id,
            version_number=2,
            status="draft",
        )
        await _seed_tier_version(
            db,
            tier_version_id=archived_version_id,
            tier_id=tier_id,
            version_number=3,
            status="archived",
        )

        await _seed_assignment(
            db,
            assignment_id=f"assignment-active-{suffix}",
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=active_version_id,
        )

        for version_id in (draft_version_id, archived_version_id):
            with pytest.raises(Exception):
                await _seed_assignment(
                    db,
                    assignment_id=f"assignment-rejected-{version_id}",
                    organization_id=organization_id,
                    tier_id=tier_id,
                    tier_version_id=version_id,
                )

        rows = await db.query_raw(
            """
            SELECT COUNT(*)::int AS total
            FROM deltallm_organizationtierassignment
            WHERE organization_id = $1
            """,
            organization_id,
        )
        assert int(rows[0]["total"]) == 1
    finally:
        await _cleanup(db, organization_id=organization_id, tier_ids=(tier_id,))
        await db.disconnect()


@pytest.mark.asyncio
async def test_archive_trigger_rejects_active_version_with_pinned_assignment() -> None:
    db = await _connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-pinned-archive-{suffix}"
    tier_id = f"tier-pinned-archive-{suffix}"
    active_version_id = f"version-pinned-archive-{suffix}"

    try:
        await _require_tier_schema(db)
        await _seed_organization(db, organization_id=organization_id)
        await _seed_tier(db, tier_id=tier_id, tier_key=f"pinned-archive-{suffix}")
        await _seed_tier_version(
            db,
            tier_version_id=active_version_id,
            tier_id=tier_id,
            version_number=1,
            status="active",
        )
        await _seed_assignment(
            db,
            assignment_id=f"assignment-pinned-archive-{suffix}",
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=active_version_id,
        )

        with pytest.raises(Exception):
            await db.execute_raw(
                """
                UPDATE deltallm_tierversion
                SET status = 'archived',
                    updated_at = NOW()
                WHERE tier_version_id = $1
                """,
                active_version_id,
            )

        rows = await db.query_raw(
            """
            SELECT status
            FROM deltallm_tierversion
            WHERE tier_version_id = $1
            """,
            active_version_id,
        )
        assert rows[0]["status"] == "active"
    finally:
        await _cleanup(db, organization_id=organization_id, tier_ids=(tier_id,))
        await db.disconnect()
