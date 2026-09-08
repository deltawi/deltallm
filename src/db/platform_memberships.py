from __future__ import annotations

from src.auth.roles import OrganizationRole
from src.db.platform_accounts import PlatformAccountDatabase


async def seed_organization_membership(
    db: PlatformAccountDatabase, *, account_id: str, organization_id: str
) -> None:
    await db.execute_raw(
        """
        INSERT INTO deltallm_organizationmembership (
            membership_id, account_id, organization_id, role, created_at, updated_at
        )
        VALUES (gen_random_uuid(), $1, $2, $3, NOW(), NOW())
        ON CONFLICT (account_id, organization_id) DO NOTHING
        """,
        account_id,
        organization_id,
        OrganizationRole.MEMBER,
    )


async def seed_team_membership(
    db: PlatformAccountDatabase, *, account_id: str, team_id: str, role: str
) -> None:
    await db.execute_raw(
        """
        INSERT INTO deltallm_teammembership (
            membership_id, account_id, team_id, role, created_at, updated_at
        )
        VALUES (gen_random_uuid(), $1, $2, $3, NOW(), NOW())
        ON CONFLICT (account_id, team_id) DO NOTHING
        """,
        account_id,
        team_id,
        role,
    )


async def lock_sso_default_team(db: PlatformAccountDatabase, *, team_id: str) -> str | None:
    rows = await db.query_raw(
        """
        SELECT organization_id FROM deltallm_teamtable
        WHERE team_id = $1
        FOR SHARE
        """,
        team_id,
    )
    if not rows:
        raise ValueError("SSO default team is unavailable")
    return str(rows[0]["organization_id"]) if rows[0].get("organization_id") else None
