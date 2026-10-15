from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

import pytest
from prisma import Prisma

from src.auth.sso_group_settings import SSOGroupSyncSettings
from src.auth.sso_identity import SSOIdentityAssertion, SSOSubjectSource
from src.services.identity.platform_identity_service import PlatformIdentityService
from src.services.identity.sso_group_access import plan_group_access
from src.services.identity.sso_group_sync import SSOGroupSyncService

pytestmark = pytest.mark.postgres


@dataclass
class GroupDb:
    db: Prisma
    suffix: str
    email: str
    org: str
    team_ml: str
    team_support: str
    team_manual: str


@pytest.fixture
async def group_db() -> AsyncIterator[GroupDb]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is required for PostgreSQL SSO group sync tests")
    db = Prisma(datasource={"url": url})
    await db.connect()
    suffix = uuid4().hex
    ctx = GroupDb(
        db=db,
        suffix=suffix,
        email=f"group-{suffix}@example.com",
        org=f"org-{suffix}",
        team_ml=f"ml-{suffix}",
        team_support=f"support-{suffix}",
        team_manual=f"manual-{suffix}",
    )
    await db.execute_raw(
        "INSERT INTO deltallm_organizationtable (id, organization_id, updated_at) "
        "VALUES (gen_random_uuid(), $1, NOW())",
        ctx.org,
    )
    for team_id in (ctx.team_ml, ctx.team_support, ctx.team_manual):
        await db.execute_raw(
            "INSERT INTO deltallm_teamtable (team_id, organization_id, models, updated_at) "
            "VALUES ($1, $2, ARRAY[]::text[], NOW())",
            team_id,
            ctx.org,
        )
    try:
        yield ctx
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_verificationtoken WHERE team_id = ANY($1::text[])",
            [ctx.team_ml, ctx.team_support, ctx.team_manual],
        )
        await db.execute_raw(
            "DELETE FROM deltallm_platformaccount WHERE email LIKE $1", f"%{suffix}@example.com"
        )
        await db.execute_raw(
            "DELETE FROM deltallm_teamtable WHERE team_id = ANY($1::text[])",
            [ctx.team_ml, ctx.team_support, ctx.team_manual],
        )
        await db.execute_raw(
            "DELETE FROM deltallm_organizationtable WHERE organization_id = $1", ctx.org
        )
        await db.disconnect()


def _settings(ctx: GroupDb, *, remove: bool) -> SSOGroupSyncSettings:
    return SSOGroupSyncSettings.model_validate(
        {
            "enabled": True,
            "claim": "deltallm_role",
            "admin_values": ["admin"],
            "remove_unlisted": remove,
            "mappings": [
                {"value": "team-ml", "team_id": ctx.team_ml, "team_role": "team_developer"},
                {"value": "support", "team_id": ctx.team_support, "team_role": "team_viewer"},
            ],
        }
    )


def _identity(ctx: GroupDb, email: str | None = None) -> SSOIdentityAssertion:
    email = email or ctx.email
    return SSOIdentityAssertion(
        provider="oidc",
        subject=f"sub-{email}",
        email=email,
        email_verified=True,
        subject_source=SSOSubjectSource.PROVIDER,
    )


async def _team_roles(ctx: GroupDb, account_id: str) -> dict[str, str]:
    rows = await ctx.db.query_raw(
        "SELECT team_id, role FROM deltallm_teammembership WHERE account_id = $1", account_id
    )
    return {str(row["team_id"]): str(row["role"]) for row in rows}


async def _role(ctx: GroupDb, account_id: str) -> str:
    return await _role_in(ctx.db, account_id)


async def _role_in(db: object, account_id: str) -> str:
    rows = await db.query_raw(  # type: ignore[attr-defined]
        "SELECT role FROM deltallm_platformaccount WHERE account_id = $1", account_id
    )
    return str(rows[0]["role"])


@pytest.mark.asyncio
async def test_sso_login_grants_mapped_access_then_removes_it(group_db: GroupDb) -> None:
    ctx = group_db
    service = PlatformIdentityService(ctx.db, salt="test-salt")
    first = await service.upsert_sso_account(
        identity=_identity(ctx),
        is_platform_admin=False,
        group_access=plan_group_access(_settings(ctx, remove=False), ["team-ml", "support"]),
    )
    assert first is not None and first.group_sync is not None
    account_id = first.context.account_id
    assert await _team_roles(ctx, account_id) == {
        ctx.team_ml: "team_developer",
        ctx.team_support: "team_viewer",
    }
    org_rows = await ctx.db.query_raw(
        "SELECT role FROM deltallm_organizationmembership WHERE account_id = $1", account_id
    )
    assert [row["role"] for row in org_rows] == ["org_member"]

    # A manual membership in an unmapped team and a key in the support team.
    await ctx.db.execute_raw(
        "INSERT INTO deltallm_teammembership (membership_id, account_id, team_id, role, "
        "created_at, updated_at) VALUES (gen_random_uuid(), $1, $2, 'team_admin', NOW(), NOW())",
        account_id,
        ctx.team_manual,
    )
    token = f"tok-{ctx.suffix}"
    await ctx.db.execute_raw(
        "INSERT INTO deltallm_verificationtoken (id, token, team_id, owner_account_id, models, "
        "updated_at) VALUES (gen_random_uuid(), $1, $2, $3, ARRAY[]::text[], NOW())",
        token,
        ctx.team_support,
        account_id,
    )

    # Add-only mode keeps support even without the value.
    kept = await service.upsert_sso_account(
        identity=_identity(ctx),
        is_platform_admin=False,
        group_access=plan_group_access(_settings(ctx, remove=False), ["team-ml"]),
    )
    assert kept is not None and kept.group_sync is not None
    assert kept.group_sync.diff.changes == ()
    assert ctx.team_support in await _team_roles(ctx, account_id)

    removed = await service.upsert_sso_account(
        identity=_identity(ctx),
        is_platform_admin=False,
        group_access=plan_group_access(_settings(ctx, remove=True), ["team-ml"]),
    )
    assert removed is not None and removed.group_sync is not None
    assert removed.group_sync.revoked_key_hashes == (token,)
    assert await _team_roles(ctx, account_id) == {
        ctx.team_ml: "team_developer",
        ctx.team_manual: "team_admin",
    }
    keys = await ctx.db.query_raw(
        "SELECT token FROM deltallm_verificationtoken WHERE token = $1", token
    )
    assert keys == []


@pytest.mark.asyncio
async def test_admin_value_promotes_and_removal_keeps_last_admin(group_db: GroupDb) -> None:
    ctx = group_db
    service = PlatformIdentityService(ctx.db, salt="test-salt")
    existing = await service.ensure_account(email=ctx.email, role="org_user", is_active=True)
    account_id = existing["account_id"]
    promoted = await service.upsert_sso_account(
        identity=_identity(ctx),
        is_platform_admin=False,
        group_access=plan_group_access(_settings(ctx, remove=True), ["admin"]),
    )
    assert promoted is not None and promoted.context.role == "platform_admin"

    class _Rollback(Exception):
        pass

    # Other tests can leave admins in the shared database: hide them in a rolled-back transaction.
    with pytest.raises(_Rollback):
        async with ctx.db.tx() as tx:
            await tx.execute_raw(
                "UPDATE deltallm_platformaccount SET is_active = false "
                "WHERE role = 'platform_admin' AND account_id <> $1",
                account_id,
            )
            kept = await SSOGroupSyncService(tx).apply(
                plan_group_access(_settings(ctx, remove=True), []), account_id
            )
            assert [c.action for c in kept.diff.changes] == ["kept"]
            assert await _role_in(tx, account_id) == "platform_admin"
            raise _Rollback

    second = await service.ensure_account(
        email=f"second-{ctx.suffix}@example.com", role="platform_admin", is_active=True
    )
    assert second["account_id"] != account_id
    demoted = await service.upsert_sso_account(
        identity=_identity(ctx),
        is_platform_admin=False,
        group_access=plan_group_access(_settings(ctx, remove=True), []),
    )
    assert demoted is not None and demoted.context.role == "org_user"


@pytest.mark.asyncio
async def test_preview_writes_nothing(group_db: GroupDb) -> None:
    ctx = group_db
    service = PlatformIdentityService(ctx.db, salt="test-salt")
    existing = await service.ensure_account(email=ctx.email, role="org_user", is_active=True)
    plan = plan_group_access(_settings(ctx, remove=True), ["team-ml", "admin"])
    view = await SSOGroupSyncService(ctx.db).preview(plan, existing["account_id"])
    assert {(c.kind, c.action) for c in view.diff.changes} == {
        ("platform_role", "update"),
        ("team", "add"),
        ("organization", "add"),
    }
    assert await _team_roles(ctx, existing["account_id"]) == {}
    assert await _role(ctx, existing["account_id"]) == "org_user"


@pytest.mark.asyncio
async def test_removal_refused_by_deferred_trigger_keeps_membership(
    group_db: GroupDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = group_db
    trigger = f"sso_test_block_{ctx.suffix}"
    # Stand-in for the credential audience triggers: refuse removal from the support team.
    await ctx.db.execute_raw(
        f"""
        CREATE FUNCTION "{trigger}"() RETURNS trigger LANGUAGE plpgsql AS $fn$
        BEGIN
          IF OLD.team_id = '{ctx.team_support}' THEN
            RAISE EXCEPTION 'credential still shared' USING ERRCODE = '23514';
          END IF;
          RETURN OLD;
        END $fn$
        """
    )
    await ctx.db.execute_raw(
        f'CREATE CONSTRAINT TRIGGER "{trigger}" AFTER DELETE ON deltallm_teammembership '
        f'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "{trigger}"()'
    )
    monkeypatch.setattr(
        "src.db.identity.sso_group_sync.MEMBERSHIP_AUDIENCE_CONSTRAINTS",
        ("deltallm_teammembership_credential_audience_trg", trigger),
    )
    try:
        service = PlatformIdentityService(ctx.db, salt="test-salt")
        first = await service.upsert_sso_account(
            identity=_identity(ctx),
            is_platform_admin=False,
            group_access=plan_group_access(_settings(ctx, remove=False), ["team-ml", "support"]),
        )
        assert first is not None
        account_id = first.context.account_id

        login = await service.upsert_sso_account(
            identity=_identity(ctx),
            is_platform_admin=False,
            group_access=plan_group_access(_settings(ctx, remove=True), []),
        )
        assert login is not None and login.group_sync is not None
        assert [(c.target_id, c.action) for c in login.group_sync.diff.changes] == [
            (ctx.team_ml, "remove")
        ]
        assert {(s.target_id, s.reason) for s in login.group_sync.diff.skipped} == {
            (ctx.team_support, "credential_in_use")
        }
        assert await _team_roles(ctx, account_id) == {ctx.team_support: "team_viewer"}
    finally:
        await ctx.db.execute_raw(f'DROP TRIGGER "{trigger}" ON deltallm_teammembership')
        await ctx.db.execute_raw(f'DROP FUNCTION "{trigger}"()')
