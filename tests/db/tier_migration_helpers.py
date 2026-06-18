from __future__ import annotations

from datetime import datetime
import os
from typing import Any

import pytest

try:
    from prisma import Prisma
except Exception:  # pragma: no cover
    Prisma = None  # type: ignore[assignment]


DATABASE_URL = os.getenv("DATABASE_URL")


async def connect_prisma() -> Any:
    if Prisma is None or not DATABASE_URL:  # pragma: no cover
        pytest.skip("DATABASE_URL and prisma client are required for DB-backed tier tests")
    client = Prisma(datasource={"url": DATABASE_URL})
    await client.connect()
    return client


async def require_tier_schema(db: Any) -> None:
    for relation_name in (
        "deltallm_tier",
        "deltallm_tierversion",
        "deltallm_organizationtierassignment",
    ):
        rows = await db.query_raw(
            "SELECT to_regclass($1)::text AS relation_name",
            f"public.{relation_name}",
        )
        if not rows or rows[0].get("relation_name") is None:
            pytest.skip("Tier tables are missing; run Prisma migrations before this test")

    for constraint_name in (
        "deltallm_tierversion_id_tier_key",
        "deltallm_tierversion_version_number_check",
        "deltallm_tierversion_draft_publish_metadata_check",
        "deltallm_orgtierassignment_version_matches_tier_fkey",
        "deltallm_tiermodelpolicy_capacity_pool_fkey",
        "deltallm_orgtierassignment_primary_no_overlap",
    ):
        rows = await db.query_raw(
            """
            SELECT 1
            FROM pg_constraint
            WHERE conname = $1
            """,
            constraint_name,
        )
        assert rows, f"missing tier invariant constraint: {constraint_name}"

    rows = await db.query_raw(
        """
        SELECT 1
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND indexname = 'deltallm_tierversion_one_active_per_tier'
        """
    )
    assert rows, "missing one-active-version-per-tier index"

    for trigger_name in (
        "deltallm_orgtierassignment_active_version_guard",
        "deltallm_tierversion_retire_assignment_guard",
    ):
        rows = await db.query_raw(
            """
            SELECT 1
            FROM pg_trigger
            WHERE tgname = $1
              AND NOT tgisinternal
            """,
            trigger_name,
        )
        assert rows, f"missing tier invariant trigger: {trigger_name}"


async def seed_organization(db: Any, *, organization_id: str) -> None:
    await db.execute_raw(
        """
        INSERT INTO deltallm_organizationtable (
            organization_id,
            organization_name,
            created_at,
            updated_at
        )
        VALUES ($1, $2, NOW(), NOW())
        """,
        organization_id,
        "Tier invariant test org",
    )


async def seed_tier(db: Any, *, tier_id: str, tier_key: str) -> None:
    await db.execute_raw(
        """
        INSERT INTO deltallm_tier (
            tier_id,
            tier_key,
            name,
            enabled,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, TRUE, NOW(), NOW())
        """,
        tier_id,
        tier_key,
        f"Test {tier_key}",
    )


async def seed_tier_version(
    db: Any,
    *,
    tier_version_id: str,
    tier_id: str,
    version_number: int,
    status: str,
) -> None:
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
        VALUES ($1, $2, $3, $4, NOW(), NOW())
        """,
        tier_version_id,
        tier_id,
        version_number,
        status,
    )


async def seed_assignment(
    db: Any,
    *,
    assignment_id: str,
    organization_id: str,
    tier_id: str,
    tier_version_id: str | None,
    assignment_type: str = "primary",
    enabled: bool = True,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> None:
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
            starts_at,
            ends_at,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, 1, $7::timestamp, $8::timestamp, NOW(), NOW())
        """,
        assignment_id,
        organization_id,
        tier_id,
        tier_version_id,
        assignment_type,
        enabled,
        starts_at,
        ends_at,
    )


async def seed_capacity_pool(
    db: Any,
    *,
    tier_version_id: str,
    pool_key: str,
    callable_key: str,
) -> None:
    await db.execute_raw(
        """
        INSERT INTO deltallm_tiercapacitypool (
            tier_capacity_pool_id,
            tier_version_id,
            pool_key,
            callable_key,
            rpm_capacity,
            created_at,
            updated_at
        )
        VALUES (gen_random_uuid()::text, $1, $2, $3, 1000, NOW(), NOW())
        """,
        tier_version_id,
        pool_key,
        callable_key,
    )


async def seed_model_policy(
    db: Any,
    *,
    tier_version_id: str,
    callable_key: str,
    capacity_pool_key: str | None,
) -> None:
    await db.execute_raw(
        """
        INSERT INTO deltallm_tiermodelpolicy (
            tier_model_policy_id,
            tier_version_id,
            callable_key,
            enabled,
            access_mode,
            capacity_pool_key,
            created_at,
            updated_at
        )
        VALUES (gen_random_uuid()::text, $1, $2, TRUE, 'allow', $3, NOW(), NOW())
        """,
        tier_version_id,
        callable_key,
        capacity_pool_key,
    )


async def cleanup(
    db: Any,
    *,
    organization_id: str | None = None,
    tier_ids: tuple[str, ...] = (),
) -> None:
    if organization_id is not None:
        await db.execute_raw(
            """
            DELETE FROM deltallm_organizationtierassignment
            WHERE organization_id = $1
            """,
            organization_id,
        )

    for tier_id in tier_ids:
        await db.execute_raw(
            """
            DELETE FROM deltallm_tier
            WHERE tier_id = $1
            """,
            tier_id,
        )

    if organization_id is not None:
        await db.execute_raw(
            """
            DELETE FROM deltallm_organizationtable
            WHERE organization_id = $1
            """,
            organization_id,
        )
