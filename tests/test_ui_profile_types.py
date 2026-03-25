from __future__ import annotations

from datetime import UTC, datetime

import pytest


class DummyIdentityService:
    def __init__(self) -> None:
        self.admin_password_calls: list[tuple[str, str]] = []

    def validate_password_policy(self, raw_password: str) -> None:
        if len(raw_password or "") < 12:
            raise ValueError("password must be at least 12 characters")

    async def change_password(self, account_id: str, new_password: str, current_password: str | None = None):
        return None

    async def admin_set_password(self, *, account_id: str, new_password: str) -> bool:
        self.admin_password_calls.append((account_id, new_password))
        return True


class FakeDB:
    def __init__(self) -> None:
        now = datetime.now(tz=UTC)
        self.accounts: dict[str, dict] = {
            "acct-1": {
                "account_id": "acct-1",
                "email": "owner@example.com",
                "role": "org_user",
                "is_active": True,
                "force_password_change": False,
                "mfa_enabled": False,
                "created_at": now,
                "updated_at": now,
                "last_login_at": None,
            }
        }
        self.org_memberships: dict[str, dict] = {}
        self.team_memberships: dict[str, dict] = {}

    async def execute_raw(self, query: str, *params):
        if "INSERT INTO deltallm_platformaccount" in query:
            email, role, is_active = params
            account = next((a for a in self.accounts.values() if a["email"].lower() == str(email).lower()), None)
            now = datetime.now(tz=UTC)
            if account is None:
                account_id = f"acct-{len(self.accounts) + 1}"
                self.accounts[account_id] = {
                    "account_id": account_id,
                    "email": str(email),
                    "role": str(role),
                    "is_active": bool(is_active),
                    "force_password_change": False,
                    "mfa_enabled": False,
                    "created_at": now,
                    "updated_at": now,
                    "last_login_at": None,
                }
            else:
                account["role"] = str(role)
                account["is_active"] = bool(is_active)
                account["updated_at"] = now
            return 1

        if "INSERT INTO deltallm_organizationmembership" in query:
            account_id, organization_id, role = params
            self.org_memberships[f"om-{len(self.org_memberships) + 1}"] = {
                "membership_id": f"om-{len(self.org_memberships) + 1}",
                "account_id": str(account_id),
                "organization_id": str(organization_id),
                "role": str(role),
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
            }
            return 1

        if "INSERT INTO deltallm_teammembership" in query:
            account_id, team_id, role = params
            self.team_memberships[f"tm-{len(self.team_memberships) + 1}"] = {
                "membership_id": f"tm-{len(self.team_memberships) + 1}",
                "account_id": str(account_id),
                "team_id": str(team_id),
                "role": str(role),
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
            }
            return 1

        return 1

    async def query_raw(self, query: str, *params):
        if "FROM deltallm_platformaccount" in query and "WHERE lower(email)=lower($1)" in query:
            email = str(params[0]).lower()
            for row in self.accounts.values():
                if str(row.get("email") or "").lower() == email:
                    return [row]
            return []

        if "FROM deltallm_platformaccount" in query:
            return list(self.accounts.values())

        if "FROM deltallm_organizationmembership" in query and "WHERE account_id = $1" in query:
            account_id = str(params[0])
            return [m for m in self.org_memberships.values() if m["account_id"] == account_id]

        if "FROM deltallm_organizationmembership" in query:
            return list(self.org_memberships.values())

        if "FROM deltallm_teammembership" in query and "WHERE account_id = $1" in query:
            account_id = str(params[0])
            return [m for m in self.team_memberships.values() if m["account_id"] == account_id]

        if "FROM deltallm_teammembership" in query:
            return list(self.team_memberships.values())

        return []


@pytest.mark.asyncio
async def test_create_rbac_account_invalid_role_rejected(client, test_app):
    fake_db = FakeDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.platform_identity_service = DummyIdentityService()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/rbac/accounts",
        headers={"Authorization": "Bearer mk-test"},
        json={"email": "test@example.com", "role": "team_admin"},
    )

    assert response.status_code == 400
    assert "invalid role" in response.text


@pytest.mark.asyncio
async def test_create_rbac_account_short_password_rejected_before_insert(client, test_app):
    fake_db = FakeDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.platform_identity_service = DummyIdentityService()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/rbac/accounts",
        headers={"Authorization": "Bearer mk-test"},
        json={"email": "shortpass@example.com", "password": "Budget123!", "role": "org_user"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "password must be at least 12 characters"
    assert "shortpass@example.com" not in {account["email"] for account in fake_db.accounts.values()}


@pytest.mark.asyncio
async def test_create_rbac_account_uses_admin_password_setter(client, test_app):
    fake_db = FakeDB()
    identity_service = DummyIdentityService()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.platform_identity_service = identity_service
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/rbac/accounts",
        headers={"Authorization": "Bearer mk-test"},
        json={"email": "owner@example.com", "password": "very-secure-password", "role": "org_user"},
    )

    assert response.status_code == 200
    assert identity_service.admin_password_calls == [("acct-1", "very-secure-password")]


@pytest.mark.asyncio
async def test_create_org_membership_invalid_role_rejected(client, test_app):
    fake_db = FakeDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.platform_identity_service = DummyIdentityService()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/rbac/organization-memberships",
        headers={"Authorization": "Bearer mk-test"},
        json={"account_id": "acct-1", "organization_id": "org-1", "role": "team_viewer"},
    )

    assert response.status_code == 400
    assert "invalid organization role" in response.text


@pytest.mark.asyncio
async def test_create_team_membership_invalid_role_rejected(client, test_app):
    fake_db = FakeDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.platform_identity_service = DummyIdentityService()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/rbac/team-memberships",
        headers={"Authorization": "Bearer mk-test"},
        json={"account_id": "acct-1", "team_id": "team-1", "role": "org_owner"},
    )

    assert response.status_code == 400
    assert "invalid team role" in response.text
