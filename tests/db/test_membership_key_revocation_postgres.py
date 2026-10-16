from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
import os
from uuid import uuid4

import pytest
from prisma import Prisma

from src.api.admin.membership_key_revocation import (
    delete_all_owned_keys,
    delete_keys_owned_in_organization,
    delete_keys_owned_in_team,
)

pytestmark = pytest.mark.postgres


class _RollbackFixture(Exception):
    pass


@pytest.fixture
async def key_database() -> AsyncIterator[tuple[Prisma, str]]:
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("The PostgreSQL lane requires DATABASE_URL")
        pytest.skip("DATABASE_URL is required")
    db = Prisma(datasource={"url": url})
    await db.connect()
    prefix = "member-keys-" + uuid4().hex[:12]
    try:
        async with db.tx(timeout=timedelta(seconds=30)) as tx:
            await _seed(tx, prefix)
            yield tx, prefix
            # Roll back all fixture rows, including when a test fails.
            raise _RollbackFixture()
    except _RollbackFixture:
        pass
    finally:
        await db.disconnect()


async def _seed(db: Prisma, prefix: str) -> None:
    for org in ("org-a", "org-b"):
        await db.execute_raw(
            "INSERT INTO deltallm_organizationtable (id, organization_id, updated_at) VALUES ($1, $1, NOW())",
            f"{prefix}-{org}",
        )
    for team, org in (("team-a1", "org-a"), ("team-a2", "org-a"), ("team-b1", "org-b")):
        await db.execute_raw(
            "INSERT INTO deltallm_teamtable (team_id, organization_id) VALUES ($1, $2)",
            f"{prefix}-{team}",
            f"{prefix}-{org}",
        )
    for account in ("me", "peer"):
        await db.execute_raw(
            "INSERT INTO deltallm_platformaccount (account_id, email) VALUES ($1, $1 || '@example.com')",
            f"{prefix}-{account}",
        )
    keys = (
        # (key, team, owner)
        ("me-a1", "team-a1", "me"),
        ("me-a2", "team-a2", "me"),
        ("me-b1", "team-b1", "me"),
        ("me-no-team", None, "me"),
        ("peer-a1", "team-a1", "peer"),
        ("unowned-a1", "team-a1", None),
    )
    for key, team, owner in keys:
        await db.execute_raw(
            """INSERT INTO deltallm_verificationtoken (token, team_id, owner_account_id, models, updated_at)
            VALUES ($1, $2, $3, ARRAY[]::text[], NOW())""",
            f"{prefix}-{key}",
            f"{prefix}-{team}" if team else None,
            f"{prefix}-{owner}" if owner else None,
        )


async def _remaining(db: Prisma, prefix: str) -> set[str]:
    rows = await db.query_raw(
        "SELECT token FROM deltallm_verificationtoken WHERE token LIKE $1", prefix + "-%"
    )
    return {str(row["token"]).removeprefix(prefix + "-") for row in rows}


ALL_KEYS = {"me-a1", "me-a2", "me-b1", "me-no-team", "peer-a1", "unowned-a1"}


@pytest.mark.asyncio
async def test_team_removal_deletes_only_the_owners_keys_in_that_team(
    key_database: tuple[Prisma, str],
) -> None:
    db, prefix = key_database

    deleted = await delete_keys_owned_in_team(
        db, account_id=f"{prefix}-me", team_id=f"{prefix}-team-a1"
    )

    assert deleted == [f"{prefix}-me-a1"]
    assert await _remaining(db, prefix) == ALL_KEYS - {"me-a1"}


@pytest.mark.asyncio
async def test_organization_removal_deletes_the_owners_keys_in_every_team_of_it(
    key_database: tuple[Prisma, str],
) -> None:
    db, prefix = key_database

    deleted = await delete_keys_owned_in_organization(
        db, account_id=f"{prefix}-me", organization_id=f"{prefix}-org-a"
    )

    assert sorted(deleted) == [f"{prefix}-me-a1", f"{prefix}-me-a2"]
    assert await _remaining(db, prefix) == ALL_KEYS - {"me-a1", "me-a2"}


@pytest.mark.asyncio
async def test_account_deletion_deletes_every_key_the_account_owns(
    key_database: tuple[Prisma, str],
) -> None:
    db, prefix = key_database

    deleted = await delete_all_owned_keys(db, account_id=f"{prefix}-me")

    assert len(deleted) == 4
    assert await _remaining(db, prefix) == {"peer-a1", "unowned-a1"}
