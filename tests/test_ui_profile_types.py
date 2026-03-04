from __future__ import annotations

from datetime import UTC, datetime

import pytest


class DummyIdentityService:
    async def change_password(self, account_id: str, new_password: str, current_password: str | None = None):
        return None


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
