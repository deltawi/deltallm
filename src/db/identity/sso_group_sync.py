"""SQL for SSO group sync. Every function runs inside the caller's transaction."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field

from prisma.errors import RawQueryError

from src.auth.roles import PlatformRole
from src.db.identity.platform_accounts import PlatformAccountDatabase


@dataclass(frozen=True, slots=True)
class TeamTarget:
    team_id: str
    name: str | None
    organization_id: str | None
    organization_active: bool
    external_managed: bool


@dataclass(frozen=True, slots=True)
class OrganizationTarget:
    organization_id: str
    name: str | None
    active: bool


@dataclass(frozen=True, slots=True)
class AccountAccess:
    account_id: str
    role: str
    org_roles: Mapping[str, str] = field(default_factory=dict)
    team_roles: Mapping[str, str] = field(default_factory=dict)


async def load_team_targets(
    db: PlatformAccountDatabase, team_ids: Collection[str], *, lock: bool
) -> dict[str, TeamTarget]:
    if not team_ids:
        return {}
    rows = await db.query_raw(
        f"""
        SELECT t.team_id, t.team_alias, t.organization_id,
               COALESCE(o.lifecycle_state = 'active', t.organization_id IS NULL) AS organization_active,
               EXISTS (
                   SELECT 1 FROM deltallm_externalauthbinding b WHERE b.team_id = t.team_id
               ) OR EXISTS (
                   SELECT 1 FROM deltallm_externalauthbindingteam bt
                   WHERE bt.team_id = t.team_id AND bt.state = 'active'
               ) AS external_managed
        FROM deltallm_teamtable t
        LEFT JOIN deltallm_organizationtable o ON o.organization_id = t.organization_id
        WHERE t.team_id = ANY($1::text[])
        {"FOR SHARE OF t" if lock else ""}
        """,
        sorted(team_ids),
    )
    return {
        str(row["team_id"]): TeamTarget(
            team_id=str(row["team_id"]),
            name=str(row["team_alias"]) if row.get("team_alias") else None,
            organization_id=str(row["organization_id"]) if row.get("organization_id") else None,
            organization_active=row.get("organization_active") is True,
            external_managed=row.get("external_managed") is True,
        )
        for row in rows
    }


async def load_organization_targets(
    db: PlatformAccountDatabase, organization_ids: Collection[str], *, lock: bool
) -> dict[str, OrganizationTarget]:
    if not organization_ids:
        return {}
    rows = await db.query_raw(
        f"""
        SELECT organization_id, organization_name, lifecycle_state = 'active' AS active
        FROM deltallm_organizationtable
        WHERE organization_id = ANY($1::text[])
        {"FOR SHARE" if lock else ""}
        """,
        sorted(organization_ids),
    )
    return {
        str(row["organization_id"]): OrganizationTarget(
            organization_id=str(row["organization_id"]),
            name=str(row["organization_name"]) if row.get("organization_name") else None,
            active=row.get("active") is True,
        )
        for row in rows
    }


async def load_account_access(db: PlatformAccountDatabase, account_id: str) -> AccountAccess | None:
    accounts = await db.query_raw(
        "SELECT account_id, role FROM deltallm_platformaccount WHERE account_id = $1",
        account_id,
    )
    if not accounts:
        return None
    memberships = await db.query_raw(
        """
        SELECT 'organization' AS kind, organization_id AS target_id, role
        FROM deltallm_organizationmembership WHERE account_id = $1
        UNION ALL
        SELECT 'team' AS kind, team_id AS target_id, role
        FROM deltallm_teammembership WHERE account_id = $1
        """,
        account_id,
    )
    org_roles = {
        str(row["target_id"]): str(row["role"])
        for row in memberships
        if row["kind"] == "organization"
    }
    team_roles = {
        str(row["target_id"]): str(row["role"]) for row in memberships if row["kind"] == "team"
    }
    return AccountAccess(
        account_id=str(accounts[0]["account_id"]),
        role=str(accounts[0]["role"]),
        org_roles=org_roles,
        team_roles=team_roles,
    )


async def is_last_active_platform_admin(
    db: PlatformAccountDatabase, account_id: str, *, lock: bool
) -> bool:
    """With `lock`, a transaction advisory lock serializes SSO admin demotions.

    Login transactions already hold their own account row, so locking every admin
    row here could deadlock two concurrent demotions.
    """
    if lock:
        await db.query_raw(
            "SELECT pg_advisory_xact_lock(hashtext(CAST($1 AS TEXT)))::text AS locked",
            "deltallm_sso_group_admin_demotion",
        )
    rows = await db.query_raw(
        """
        SELECT account_id FROM deltallm_platformaccount
        WHERE role = $1 AND is_active = true
        LIMIT 2
        """,
        PlatformRole.ADMIN,
    )
    return not any(str(row["account_id"]) != account_id for row in rows)


async def set_platform_role(db: PlatformAccountDatabase, account_id: str, role: str) -> None:
    await db.execute_raw(
        "UPDATE deltallm_platformaccount SET role = $2, updated_at = NOW() WHERE account_id = $1",
        account_id,
        role,
    )


async def upsert_organization_membership(
    db: PlatformAccountDatabase, *, account_id: str, organization_id: str, role: str
) -> None:
    await db.execute_raw(
        """
        INSERT INTO deltallm_organizationmembership (
            membership_id, account_id, organization_id, role, created_at, updated_at
        )
        VALUES (gen_random_uuid(), $1, $2, $3, NOW(), NOW())
        ON CONFLICT (account_id, organization_id)
        DO UPDATE SET role = EXCLUDED.role, updated_at = NOW()
        """,
        account_id,
        organization_id,
        role,
    )


async def upsert_team_membership(
    db: PlatformAccountDatabase, *, account_id: str, team_id: str, role: str
) -> None:
    await db.execute_raw(
        """
        INSERT INTO deltallm_teammembership (
            membership_id, account_id, team_id, role, created_at, updated_at
        )
        VALUES (gen_random_uuid(), $1, $2, $3, NOW(), NOW())
        ON CONFLICT (account_id, team_id)
        DO UPDATE SET role = EXCLUDED.role, updated_at = NOW()
        """,
        account_id,
        team_id,
        role,
    )


async def remove_team_memberships(
    db: PlatformAccountDatabase, *, account_id: str, team_ids: Collection[str]
) -> list[str]:
    """Remove memberships and the account's keys in those teams, as a manual removal does.

    Returns the hashes of the deleted keys, so the caller can invalidate key caches.
    """

    if not team_ids:
        return []
    rows = await db.query_raw(
        """
        WITH removed AS (
            DELETE FROM deltallm_teammembership
            WHERE account_id = $1 AND team_id = ANY($2::text[])
            RETURNING team_id
        )
        DELETE FROM deltallm_verificationtoken k
        USING removed r
        WHERE k.team_id = r.team_id AND k.owner_account_id = $1
        RETURNING k.token
        """,
        account_id,
        sorted(team_ids),
    )
    return [str(row["token"]) for row in rows]


async def remove_organization_memberships(
    db: PlatformAccountDatabase, *, account_id: str, organization_ids: Collection[str]
) -> list[str]:
    """Remove org memberships, the account's team memberships in those orgs, and its keys there."""

    if not organization_ids:
        return []
    team_rows = await db.query_raw(
        """
        SELECT m.team_id FROM deltallm_teammembership m
        JOIN deltallm_teamtable t ON t.team_id = m.team_id
        WHERE m.account_id = $1 AND t.organization_id = ANY($2::text[])
        """,
        account_id,
        sorted(organization_ids),
    )
    tokens = await remove_team_memberships(
        db, account_id=account_id, team_ids=[str(row["team_id"]) for row in team_rows]
    )
    await db.execute_raw(
        """
        DELETE FROM deltallm_organizationmembership
        WHERE account_id = $1 AND organization_id = ANY($2::text[])
        """,
        account_id,
        sorted(organization_ids),
    )
    return tokens


# Deferred triggers that refuse a membership removal while a creator-owned model
# still uses a named credential shared through that membership.
MEMBERSHIP_AUDIENCE_CONSTRAINTS: tuple[str, ...] = (
    "deltallm_teammembership_credential_audience_trg",
    "deltallm_orgmembership_credential_audience_trg",
)


async def try_membership_removal(
    db: PlatformAccountDatabase, remove: Callable[[], Awaitable[list[str]]]
) -> list[str] | None:
    """Run one removal in a savepoint and check the audience triggers at once.

    Returns the deleted key hashes, or `None` when a trigger refuses the removal. The
    savepoint is then rolled back, so the sign-in still commits with the membership kept.
    """

    names = ", ".join(f'"{name}"' for name in MEMBERSHIP_AUDIENCE_CONSTRAINTS)
    await db.execute_raw("SAVEPOINT sso_group_removal")
    try:
        tokens = await remove()
        await db.execute_raw(f"SET CONSTRAINTS {names} IMMEDIATE")
    except RawQueryError as exc:
        meta = exc.meta if isinstance(exc.meta, Mapping) else {}
        if meta.get("code") != "23514":
            raise
        await db.execute_raw("ROLLBACK TO SAVEPOINT sso_group_removal")
        await db.execute_raw(f"SET CONSTRAINTS {names} DEFERRED")
        return None
    await db.execute_raw(f"SET CONSTRAINTS {names} DEFERRED")
    await db.execute_raw("RELEASE SAVEPOINT sso_group_removal")
    return tokens


async def find_account_id_by_email(db: PlatformAccountDatabase, email: str) -> str | None:
    rows = await db.query_raw(
        "SELECT account_id FROM deltallm_platformaccount WHERE email = $1 LIMIT 1",
        email.strip().lower(),
    )
    return str(rows[0]["account_id"]) if rows else None
