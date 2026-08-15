from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from src.db.tier_records import (
    to_tier_creation_request_record,
    to_version_record,
)
from src.services.tier_admin_serialization import serialize_tier_version
from tests.db.tier_migration_helpers import cleanup
from tests.db.tier_migration_helpers import connect_prisma
from tests.db.tier_migration_helpers import require_tier_schema
from tests.db.tier_migration_helpers import seed_tier


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "prisma/migrations/20260814120000_tier_version_revision_provenance/migration.sql"
)


def test_revision_provenance_migration_declares_required_invariants() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    for fragment in (
        'ADD COLUMN "configuration_revision" INTEGER NOT NULL DEFAULT 0',
        'ADD COLUMN "created_by_kind" TEXT NOT NULL DEFAULT \'unknown\'',
        'CONSTRAINT "deltallm_tierversion_configuration_revision_check"',
        'CONSTRAINT "deltallm_tierversion_created_by_kind_check"',
        'CONSTRAINT "deltallm_tierversion_source_not_self_check"',
        'CONSTRAINT "deltallm_tierversion_created_by_fkey"',
        'CONSTRAINT "deltallm_tierversion_source_fkey"',
        'CREATE TABLE "deltallm_tiercreationrequest"',
        'CONSTRAINT "deltallm_tiercreationrequest_principal_scope_check"',
        'CONSTRAINT "deltallm_tiercreationrequest_idempotency_key_check"',
        'CREATE UNIQUE INDEX "deltallm_tiercreationrequest_scope_key"',
        'CREATE UNIQUE INDEX "deltallm_tiercreationrequest_tier_id_key"',
        'ON DELETE CASCADE',
    ):
        assert fragment in sql


def test_revision_and_provenance_record_contracts_are_serialized() -> None:
    created_at = datetime(2026, 8, 15, 10, 30, tzinfo=UTC)
    version = to_version_record(
        {
            "tier_version_id": "version-2",
            "tier_id": "tier-1",
            "version_number": 2,
            "status": "draft",
            "configuration_revision": 7,
            "created_by_account_id": "account-1",
            "created_by_kind": "account",
            "source_tier_version_id": "version-1",
            "created_at": created_at,
            "updated_at": created_at,
        }
    )

    assert version.configuration_revision == 7
    assert version.created_by_account_id == "account-1"
    assert version.created_by_kind == "account"
    assert version.source_tier_version_id == "version-1"
    serialized = serialize_tier_version(version)
    assert {
        "configuration_revision": serialized["configuration_revision"],
        "created_by_account_id": serialized["created_by_account_id"],
        "created_by_kind": serialized["created_by_kind"],
        "source_tier_version_id": serialized["source_tier_version_id"],
    } == {
        "configuration_revision": 7,
        "created_by_account_id": "account-1",
        "created_by_kind": "account",
        "source_tier_version_id": "version-1",
    }

    request = to_tier_creation_request_record(
        {
            "tier_creation_request_id": "request-1",
            "principal_scope": "account:account-1",
            "idempotency_key": "idempotency-1",
            "request_hash": "a" * 64,
            "tier_id": "tier-1",
            "created_at": created_at,
        }
    )
    assert request.principal_scope == "account:account-1"
    assert request.idempotency_key == "idempotency-1"
    assert request.request_hash == "a" * 64
    assert request.created_at == created_at


@pytest.mark.asyncio
async def test_revision_provenance_and_creation_request_constraints_against_postgres() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    tier_id = f"tier-revision-{suffix}"
    second_tier_id = f"tier-revision-second-{suffix}"
    version_id = f"version-revision-{suffix}"
    request_id = f"request-revision-{suffix}"

    try:
        await require_tier_schema(db)
        await seed_tier(db, tier_id=tier_id, tier_key=f"revision-{suffix}")
        await seed_tier(db, tier_id=second_tier_id, tier_key=f"revision-second-{suffix}")

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
            VALUES ($1, $2, 1, 'draft', NOW(), NOW())
            """,
            version_id,
            tier_id,
        )
        version_rows = await db.query_raw(
            """
            SELECT configuration_revision, created_by_kind
            FROM deltallm_tierversion
            WHERE tier_version_id = $1
            """,
            version_id,
        )
        assert int(version_rows[0]["configuration_revision"]) == 0
        assert version_rows[0]["created_by_kind"] == "unknown"

        with pytest.raises(Exception):
            await db.execute_raw(
                """
                UPDATE deltallm_tierversion
                SET configuration_revision = -1
                WHERE tier_version_id = $1
                """,
                version_id,
            )

        with pytest.raises(Exception):
            await db.execute_raw(
                """
                UPDATE deltallm_tierversion
                SET created_by_kind = 'invalid'
                WHERE tier_version_id = $1
                """,
                version_id,
            )

        with pytest.raises(Exception):
            await db.execute_raw(
                """
                UPDATE deltallm_tierversion
                SET source_tier_version_id = tier_version_id
                WHERE tier_version_id = $1
                """,
                version_id,
            )

        await db.execute_raw(
            """
            INSERT INTO deltallm_tiercreationrequest (
                tier_creation_request_id,
                principal_scope,
                idempotency_key,
                request_hash,
                tier_id,
                created_at
            )
            VALUES ($1, 'account:account-1', 'request-key', $2, $3, NOW())
            """,
            request_id,
            "a" * 64,
            tier_id,
        )

        with pytest.raises(Exception):
            await db.execute_raw(
                """
                INSERT INTO deltallm_tiercreationrequest (
                    tier_creation_request_id,
                    principal_scope,
                    idempotency_key,
                    request_hash,
                    tier_id,
                    created_at
                )
                VALUES ($1, 'account:account-1', 'request-key', $2, $3, NOW())
                """,
                f"request-duplicate-{suffix}",
                "a" * 64,
                second_tier_id,
            )

        with pytest.raises(Exception):
            await db.execute_raw(
                """
                INSERT INTO deltallm_tiercreationrequest (
                    tier_creation_request_id,
                    principal_scope,
                    idempotency_key,
                    request_hash,
                    tier_id,
                    created_at
                )
                VALUES ($1, 'account:account-2', '', $2, $3, NOW())
                """,
                f"request-blank-{suffix}",
                "b" * 64,
                second_tier_id,
            )

        await db.execute_raw('DELETE FROM deltallm_tier WHERE tier_id = $1', tier_id)
        request_rows = await db.query_raw(
            """
            SELECT COUNT(*)::int AS total
            FROM deltallm_tiercreationrequest
            WHERE tier_creation_request_id = $1
            """,
            request_id,
        )
        assert int(request_rows[0]["total"]) == 0
    finally:
        await cleanup(db, tier_ids=(tier_id, second_tier_id))
        await db.disconnect()
