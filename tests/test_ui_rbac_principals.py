from __future__ import annotations

from datetime import UTC, datetime

import pytest


class FakeDB:
    def __init__(self) -> None:
        now = datetime.now(tz=UTC)
        self.accounts: dict[str, dict] = {
            "acct-1": {
                "account_id": "acct-1",
                "email": "alice@example.com",
                "role": "platform_admin",
                "is_active": True,
                "force_password_change": False,
                "mfa_enabled": False,
                "created_at": now,
                "updated_at": now,
                "last_login_at": None,
            }
        }
        self.org_memberships: dict[str, dict] = {
            "om-1": {
                "membership_id": "om-1",
                "account_id": "acct-1",
                "organization_id": "org-1",
                "role": "org_owner",
                "created_at": now,
                "updated_at": now,
            }
        }
        self.team_memberships: dict[str, dict] = {
            "tm-1": {
                "membership_id": "tm-1",
                "account_id": "acct-1",
                "team_id": "team-1",
                "role": "team_admin",
                "created_at": now,
                "updated_at": now,
            }
        }
        self.sessions: list[dict] = [{"account_id": "acct-1"}]
        self.identities: list[dict] = [{"account_id": "acct-1"}]

    async def query_raw(self, query: str, *params):
        if "SELECT COUNT(*) AS total FROM deltallm_platformaccount" in query:
            return [{"total": len(self.accounts)}]
        if "FROM deltallm_platformaccount" in query and "WHERE account_id = $1" in query:
            row = self.accounts.get(str(params[0]))
            return [row] if row else []
        if "FROM deltallm_platformaccount" in query and "WHERE lower(email)=lower($1)" in query:
            email = str(params[0]).lower()
            for row in self.accounts.values():
                if str(row.get("email") or "").lower() == email:
                    return [row]
            return []
        if "FROM deltallm_teamtable WHERE team_id = $1" in query:
            if str(params[0]) == "team-1":
                return [{"team_id": "team-1", "organization_id": "org-1"}]
            return []
        if "FROM deltallm_organizationmembership" in query and "WHERE account_id = $1 AND organization_id = $2" in query:
            account_id = str(params[0])
            organization_id = str(params[1])
            for row in self.org_memberships.values():
                if row["account_id"] == account_id and row["organization_id"] == organization_id:
                    return [row]
            return []
        if "FROM deltallm_platformaccount" in query:
            return list(self.accounts.values())
        if "FROM deltallm_organizationmembership" in query and "WHERE membership_id = $1" in query:
            row = self.org_memberships.get(str(params[0]))
            return [row] if row else []
        if "FROM deltallm_organizationmembership" in query:
            return list(self.org_memberships.values())
        if "FROM deltallm_teammembership" in query:
            return list(self.team_memberships.values())
        return []

    async def execute_raw(self, query: str, *params):
        if "DELETE FROM deltallm_teammembership WHERE membership_id = $1" in query:
            return 1 if self.team_memberships.pop(str(params[0]), None) else 0
        if "DELETE FROM deltallm_organizationmembership WHERE membership_id = $1" in query:
            return 1 if self.org_memberships.pop(str(params[0]), None) else 0
        if "DELETE FROM deltallm_teammembership" in query and "team_id IN (" in query:
            account_id = str(params[0])
            organization_id = str(params[1])
            if organization_id != "org-1":
                return 0
            to_delete = [k for k, v in self.team_memberships.items() if v["account_id"] == account_id and v["team_id"] == "team-1"]
            for k in to_delete:
                del self.team_memberships[k]
            return len(to_delete)

        if "DELETE FROM deltallm_teammembership WHERE account_id = $1" in query:
            account_id = str(params[0])
            to_delete = [k for k, v in self.team_memberships.items() if v["account_id"] == account_id]
            for k in to_delete:
                del self.team_memberships[k]
            return len(to_delete)

        if "DELETE FROM deltallm_organizationmembership WHERE account_id = $1" in query:
            account_id = str(params[0])
            to_delete = [k for k, v in self.org_memberships.items() if v["account_id"] == account_id]
            for k in to_delete:
                del self.org_memberships[k]
            return len(to_delete)

        if "DELETE FROM deltallm_platformsession WHERE account_id = $1" in query:
            account_id = str(params[0])
            before = len(self.sessions)
            self.sessions = [s for s in self.sessions if s["account_id"] != account_id]
            return before - len(self.sessions)

        if "DELETE FROM deltallm_platformidentity WHERE account_id = $1" in query:
            account_id = str(params[0])
            before = len(self.identities)
            self.identities = [i for i in self.identities if i["account_id"] != account_id]
            return before - len(self.identities)

        if "DELETE FROM deltallm_platformaccount WHERE account_id = $1" in query:
            return 1 if self.accounts.pop(str(params[0]), None) else 0

        return 1


@pytest.mark.asyncio
async def test_list_principals_returns_account_with_memberships(client, test_app):
    fake_db = FakeDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.get("/ui/api/principals", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    principals = payload["data"]
    assert len(principals) == 1
    assert payload["pagination"]["total"] == 1
    assert principals[0]["email"] == "alice@example.com"
    assert len(principals[0]["organization_memberships"]) == 1
    assert len(principals[0]["team_memberships"]) == 1


@pytest.mark.asyncio
async def test_delete_org_membership_removes_org_and_team_memberships_in_org(client, test_app):
    fake_db = FakeDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.delete("/ui/api/rbac/organization-memberships/om-1", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert response.json()["team_memberships_removed"] == 1
    assert "om-1" not in fake_db.org_memberships
    assert "tm-1" not in fake_db.team_memberships


@pytest.mark.asyncio
async def test_delete_team_membership_removes_membership(client, test_app):
    fake_db = FakeDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.delete("/ui/api/rbac/team-memberships/tm-1", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert "tm-1" not in fake_db.team_memberships


@pytest.mark.asyncio
async def test_delete_account_removes_memberships_sessions_identities(client, test_app):
    fake_db = FakeDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.delete("/ui/api/rbac/accounts/acct-1", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert "acct-1" not in fake_db.accounts
    assert fake_db.org_memberships == {}
    assert fake_db.team_memberships == {}
    assert fake_db.sessions == []
    assert fake_db.identities == []


@pytest.mark.asyncio
async def test_upsert_team_membership_requires_org_membership(client, test_app):
    fake_db = FakeDB()
    fake_db.org_memberships = {}
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/rbac/team-memberships",
        headers={"Authorization": "Bearer mk-test"},
        json={"account_id": "acct-1", "team_id": "team-1", "role": "team_viewer"},
    )

    assert response.status_code == 400
    assert "team's organization" in response.text
