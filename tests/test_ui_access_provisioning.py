from __future__ import annotations

import pytest


class FakeDB:
    def __init__(self) -> None:
        self.organizations = {"org-1": {"organization_id": "org-1"}}
        self.teams = {"team-1": {"team_id": "team-1", "organization_id": "org-1"}}
        self.principal_summary = {
            "total_accounts": 3,
            "active_accounts": 2,
            "platform_admins": 1,
            "mfa_enabled_accounts": 1,
            "organization_memberships": 4,
            "team_memberships": 2,
        }

    async def query_raw(self, query: str, *params):
        if "FROM deltallm_organizationtable" in query:
            organization_id = str(params[0])
            row = self.organizations.get(organization_id)
            return [row] if row else []
        if "FROM deltallm_teamtable" in query:
            team_id = str(params[0])
            row = self.teams.get(team_id)
            return [row] if row else []
        if "FROM deltallm_platformaccount" in query and "COUNT(*) FILTER" in query:
            return [self.principal_summary]
        if "FROM deltallm_organizationmembership" in query and "team_memberships" in query:
            return [self.principal_summary]
        return []


class StubIdentityService:
    def __init__(
        self,
        db,
        *,
        accounts: dict[str, dict] | None = None,
        organization_memberships: list[dict] | None = None,
        team_memberships: list[dict] | None = None,
    ) -> None:
        self.db = db
        self.accounts = accounts or {}
        self.organization_memberships = organization_memberships or []
        self.team_memberships = team_memberships or []

    def with_db(self, db_client):
        return StubIdentityService(
            db_client,
            accounts=self.accounts,
            organization_memberships=self.organization_memberships,
            team_memberships=self.team_memberships,
        )

    def normalize_email(self, email: str | None) -> str:
        return str(email or "").strip().lower()

    def validate_password_policy(self, raw_password: str) -> None:
        if len(raw_password or "") < 12:
            raise ValueError("password must be at least 12 characters")

    async def get_account_by_email(self, email: str):
        normalized = self.normalize_email(email)
        return next((item for item in self.accounts.values() if item["email"] == normalized), None)

    async def get_account_by_id(self, account_id: str):
        return self.accounts.get(account_id)

    async def create_account(self, *, email: str, role: str, is_active: bool, password: str):
        self.validate_password_policy(password)
        normalized = self.normalize_email(email)
        if any(item["email"] == normalized for item in self.accounts.values()):
            raise ValueError("account already exists")
        account_id = f"acct-{len(self.accounts) + 1}"
        record = {
            "account_id": account_id,
            "email": normalized,
            "role": role,
            "is_active": is_active,
        }
        self.accounts[account_id] = record
        return record

    async def upsert_organization_membership(self, *, account_id: str, organization_id: str, role: str) -> None:
        self.organization_memberships.append(
            {"account_id": account_id, "organization_id": organization_id, "role": role}
        )

    async def upsert_team_membership(self, *, account_id: str, team_id: str, role: str) -> None:
        self.team_memberships.append(
            {"account_id": account_id, "team_id": team_id, "role": role}
        )


class StubInvitationService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create_invitation(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "invitation_id": "inv-1",
            "account_id": "acct-pending",
            "email": kwargs["email"],
            "status": "sent",
            "invite_scope_type": "team" if kwargs.get("team_id") else "organization",
            "expires_at": "2026-04-01T00:00:00+00:00",
            "metadata": {},
        }


@pytest.mark.asyncio
async def test_provision_person_invite_mode_calls_invitation_service(client, test_app):
    fake_db = FakeDB()
    identity_service = StubIdentityService(fake_db)
    invitation_service = StubInvitationService()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.platform_identity_service = identity_service
    test_app.state.invitation_service = invitation_service
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/rbac/provision",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "email": "invitee@example.com",
            "mode": "invite_email",
            "organization_id": "org-1",
            "organization_role": "org_member",
        },
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "invite_email"
    assert invitation_service.calls == [
        {
            "email": "invitee@example.com",
            "invited_by_account_id": None,
            "organization_id": "org-1",
            "organization_role": "org_member",
            "team_id": None,
            "team_role": "team_viewer",
        }
    ]


@pytest.mark.asyncio
async def test_provision_person_create_account_adds_org_membership(client, test_app):
    fake_db = FakeDB()
    identity_service = StubIdentityService(fake_db)
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.platform_identity_service = identity_service
    test_app.state.invitation_service = StubInvitationService()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/rbac/provision",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "email": "new-user@example.com",
            "mode": "create_account",
            "platform_role": "org_user",
            "password": "very-secure-password",
            "organization_id": "org-1",
            "organization_role": "org_admin",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "create_account"
    assert payload["account_id"] == "acct-1"
    assert identity_service.organization_memberships == [
        {"account_id": "acct-1", "organization_id": "org-1", "role": "org_admin"}
    ]


@pytest.mark.asyncio
async def test_provision_person_create_account_team_scope_adds_parent_org_membership(client, test_app):
    fake_db = FakeDB()
    identity_service = StubIdentityService(fake_db)
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.platform_identity_service = identity_service
    test_app.state.invitation_service = StubInvitationService()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/rbac/provision",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "email": "team-user@example.com",
            "mode": "create_account",
            "platform_role": "org_user",
            "password": "very-secure-password",
            "team_id": "team-1",
            "team_role": "team_developer",
        },
    )

    assert response.status_code == 200
    assert identity_service.organization_memberships == [
        {"account_id": "acct-1", "organization_id": "org-1", "role": "org_member"}
    ]
    assert identity_service.team_memberships == [
        {"account_id": "acct-1", "team_id": "team-1", "role": "team_developer"}
    ]


@pytest.mark.asyncio
async def test_provision_person_create_account_rejects_existing_email(client, test_app):
    fake_db = FakeDB()
    identity_service = StubIdentityService(
        fake_db,
        accounts={"acct-1": {"account_id": "acct-1", "email": "existing@example.com", "role": "org_user", "is_active": True}},
    )
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.platform_identity_service = identity_service
    test_app.state.invitation_service = StubInvitationService()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/rbac/provision",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "email": "existing@example.com",
            "mode": "create_account",
            "platform_role": "org_user",
            "password": "very-secure-password",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "account already exists"


@pytest.mark.asyncio
async def test_provision_person_create_account_succeeds_without_invitation_service(client, test_app):
    fake_db = FakeDB()
    identity_service = StubIdentityService(fake_db)
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.platform_identity_service = identity_service
    test_app.state.invitation_service = None
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/rbac/provision",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "email": "manual-only@example.com",
            "mode": "create_account",
            "platform_role": "org_user",
            "password": "very-secure-password",
        },
    )

    assert response.status_code == 200
    assert response.json()["mode"] == "create_account"


@pytest.mark.asyncio
async def test_provision_person_invite_mode_requires_invitation_service(client, test_app):
    fake_db = FakeDB()
    identity_service = StubIdentityService(fake_db)
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.platform_identity_service = identity_service
    test_app.state.invitation_service = None
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/rbac/provision",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "email": "invitee@example.com",
            "mode": "invite_email",
            "organization_id": "org-1",
            "organization_role": "org_member",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Invitation service unavailable"


@pytest.mark.asyncio
async def test_principals_summary_returns_global_totals(client, test_app):
    fake_db = FakeDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.get(
        "/ui/api/principals/summary",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    assert response.json() == fake_db.principal_summary
