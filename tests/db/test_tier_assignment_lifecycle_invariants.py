from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.db.tier_migration_helpers import cleanup
from tests.db.tier_migration_helpers import connect_prisma
from tests.db.tier_migration_helpers import require_tier_schema
from tests.db.tier_migration_helpers import seed_assignment
from tests.db.tier_migration_helpers import seed_organization
from tests.db.tier_migration_helpers import seed_tier
from tests.db.tier_migration_helpers import seed_tier_version


def _relative_timestamp(*, days: int) -> datetime:
    return (datetime.now(UTC) + timedelta(days=days)).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_assignment_trigger_requires_active_version_for_unpinned_enabled_assignment() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-unpinned-assignment-{suffix}"
    tier_id = f"tier-unpinned-assignment-{suffix}"
    active_version_id = f"version-unpinned-assignment-{suffix}"

    try:
        await require_tier_schema(db)
        await seed_organization(db, organization_id=organization_id)
        await seed_tier(db, tier_id=tier_id, tier_key=f"unpinned-assignment-{suffix}")

        await seed_assignment(
            db,
            assignment_id=f"assignment-disabled-unpinned-{suffix}",
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=None,
            enabled=False,
        )

        with pytest.raises(Exception):
            await seed_assignment(
                db,
                assignment_id=f"assignment-rejected-unpinned-{suffix}",
                organization_id=organization_id,
                tier_id=tier_id,
                tier_version_id=None,
                enabled=True,
            )

        await seed_tier_version(
            db,
            tier_version_id=active_version_id,
            tier_id=tier_id,
            version_number=1,
            status="active",
        )
        await seed_assignment(
            db,
            assignment_id=f"assignment-enabled-unpinned-{suffix}",
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=None,
            enabled=True,
        )

        rows = await db.query_raw(
            """
            SELECT COUNT(*)::int AS total
            FROM deltallm_organizationtierassignment
            WHERE organization_id = $1
            """,
            organization_id,
        )
        assert int(rows[0]["total"]) == 2
    finally:
        await cleanup(db, organization_id=organization_id, tier_ids=(tier_id,))
        await db.disconnect()


@pytest.mark.asyncio
async def test_retire_trigger_ignores_expired_pinned_assignment() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-expired-pinned-retire-{suffix}"
    tier_id = f"tier-expired-pinned-retire-{suffix}"
    active_version_id = f"version-expired-pinned-retire-{suffix}"

    try:
        await require_tier_schema(db)
        await seed_organization(db, organization_id=organization_id)
        await seed_tier(db, tier_id=tier_id, tier_key=f"expired-pinned-retire-{suffix}")
        await seed_tier_version(
            db,
            tier_version_id=active_version_id,
            tier_id=tier_id,
            version_number=1,
            status="active",
        )
        await seed_assignment(
            db,
            assignment_id=f"assignment-expired-pinned-retire-{suffix}",
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=active_version_id,
            ends_at=_relative_timestamp(days=-1),
        )

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
        assert rows[0]["status"] == "archived"
    finally:
        await cleanup(db, organization_id=organization_id, tier_ids=(tier_id,))
        await db.disconnect()


@pytest.mark.asyncio
async def test_retire_trigger_ignores_expired_unpinned_assignment() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-expired-unpinned-retire-{suffix}"
    tier_id = f"tier-expired-unpinned-retire-{suffix}"
    active_version_id = f"version-expired-unpinned-retire-{suffix}"

    try:
        await require_tier_schema(db)
        await seed_organization(db, organization_id=organization_id)
        await seed_tier(db, tier_id=tier_id, tier_key=f"expired-unpinned-retire-{suffix}")
        await seed_tier_version(
            db,
            tier_version_id=active_version_id,
            tier_id=tier_id,
            version_number=1,
            status="active",
        )
        await seed_assignment(
            db,
            assignment_id=f"assignment-expired-unpinned-retire-{suffix}",
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=None,
            ends_at=_relative_timestamp(days=-1),
        )

        await db.execute_raw(
            """
            DELETE FROM deltallm_tierversion
            WHERE tier_version_id = $1
            """,
            active_version_id,
        )

        rows = await db.query_raw(
            """
            SELECT COUNT(*)::int AS total
            FROM deltallm_tierversion
            WHERE tier_version_id = $1
            """,
            active_version_id,
        )
        assert int(rows[0]["total"]) == 0
    finally:
        await cleanup(db, organization_id=organization_id, tier_ids=(tier_id,))
        await db.disconnect()


@pytest.mark.asyncio
async def test_retire_trigger_rejects_future_pinned_assignment() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-future-pinned-retire-{suffix}"
    tier_id = f"tier-future-pinned-retire-{suffix}"
    active_version_id = f"version-future-pinned-retire-{suffix}"

    try:
        await require_tier_schema(db)
        await seed_organization(db, organization_id=organization_id)
        await seed_tier(db, tier_id=tier_id, tier_key=f"future-pinned-retire-{suffix}")
        await seed_tier_version(
            db,
            tier_version_id=active_version_id,
            tier_id=tier_id,
            version_number=1,
            status="active",
        )
        await seed_assignment(
            db,
            assignment_id=f"assignment-future-pinned-retire-{suffix}",
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=active_version_id,
            starts_at=_relative_timestamp(days=1),
            ends_at=_relative_timestamp(days=2),
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
        await cleanup(db, organization_id=organization_id, tier_ids=(tier_id,))
        await db.disconnect()


@pytest.mark.asyncio
async def test_assignment_trigger_revalidates_when_expired_assignment_becomes_non_expired() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-expired-revalidate-{suffix}"
    tier_id = f"tier-expired-revalidate-{suffix}"
    archived_version_id = f"version-expired-revalidate-{suffix}"
    assignment_id = f"assignment-expired-revalidate-{suffix}"

    try:
        await require_tier_schema(db)
        await seed_organization(db, organization_id=organization_id)
        await seed_tier(db, tier_id=tier_id, tier_key=f"expired-revalidate-{suffix}")
        await seed_tier_version(
            db,
            tier_version_id=archived_version_id,
            tier_id=tier_id,
            version_number=1,
            status="archived",
        )
        await seed_assignment(
            db,
            assignment_id=assignment_id,
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=archived_version_id,
            ends_at=_relative_timestamp(days=-1),
        )

        with pytest.raises(Exception):
            await db.execute_raw(
                """
                UPDATE deltallm_organizationtierassignment
                SET ends_at = $2,
                    updated_at = NOW()
                WHERE assignment_id = $1
                """,
                assignment_id,
                _relative_timestamp(days=1),
            )
    finally:
        await cleanup(db, organization_id=organization_id, tier_ids=(tier_id,))
        await db.disconnect()


@pytest.mark.asyncio
async def test_retire_trigger_rejects_unpinned_assignment_without_replacement() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-unpinned-retire-{suffix}"
    tier_id = f"tier-unpinned-retire-{suffix}"
    active_version_id = f"version-unpinned-retire-{suffix}"

    try:
        await require_tier_schema(db)
        await seed_organization(db, organization_id=organization_id)
        await seed_tier(db, tier_id=tier_id, tier_key=f"unpinned-retire-{suffix}")
        await seed_tier_version(
            db,
            tier_version_id=active_version_id,
            tier_id=tier_id,
            version_number=1,
            status="active",
        )
        await seed_assignment(
            db,
            assignment_id=f"assignment-unpinned-retire-{suffix}",
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=None,
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

        with pytest.raises(Exception):
            await db.execute_raw(
                """
                DELETE FROM deltallm_tierversion
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
        await cleanup(db, organization_id=organization_id, tier_ids=(tier_id,))
        await db.disconnect()


@pytest.mark.asyncio
async def test_retire_trigger_allows_publish_style_replacement_for_unpinned_assignment() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    organization_id = f"org-unpinned-replace-{suffix}"
    tier_id = f"tier-unpinned-replace-{suffix}"
    old_version_id = f"version-old-unpinned-replace-{suffix}"
    new_version_id = f"version-new-unpinned-replace-{suffix}"

    try:
        await require_tier_schema(db)
        await seed_organization(db, organization_id=organization_id)
        await seed_tier(db, tier_id=tier_id, tier_key=f"unpinned-replace-{suffix}")
        await seed_tier_version(
            db,
            tier_version_id=old_version_id,
            tier_id=tier_id,
            version_number=1,
            status="active",
        )
        await seed_tier_version(
            db,
            tier_version_id=new_version_id,
            tier_id=tier_id,
            version_number=2,
            status="draft",
        )
        await seed_assignment(
            db,
            assignment_id=f"assignment-unpinned-replace-{suffix}",
            organization_id=organization_id,
            tier_id=tier_id,
            tier_version_id=None,
        )

        async with db.tx() as tx:
            await tx.execute_raw(
                """
                UPDATE deltallm_tierversion
                SET status = 'archived',
                    updated_at = NOW()
                WHERE tier_version_id = $1
                """,
                old_version_id,
            )
            await tx.execute_raw(
                """
                UPDATE deltallm_tierversion
                SET status = 'active',
                    updated_at = NOW()
                WHERE tier_version_id = $1
                """,
                new_version_id,
            )

        rows = await db.query_raw(
            """
            SELECT tier_version_id, status
            FROM deltallm_tierversion
            WHERE tier_id = $1
            ORDER BY version_number ASC
            """,
            tier_id,
        )
        assert [(row["tier_version_id"], row["status"]) for row in rows] == [
            (old_version_id, "archived"),
            (new_version_id, "active"),
        ]
    finally:
        await cleanup(db, organization_id=organization_id, tier_ids=(tier_id,))
        await db.disconnect()
