from __future__ import annotations

import pytest

from datetime import UTC, datetime


class FakeDB:
    def __init__(self) -> None:
        self.organizations: dict[str, dict] = {}
        self.teams: dict[str, dict] = {}
        self.users: dict[str, dict] = {}

    async def execute_raw(self, query: str, *params):
        if "INSERT INTO deltallm_organizationtable" in query:
            organization_id = str(params[0])
            self.organizations[organization_id] = {"organization_id": organization_id}
            return 1

        if "INSERT INTO deltallm_teamtable" in query:
            team_id = str(params[0])
            organization_id = str(params[2])
            self.teams[team_id] = {"team_id": team_id, "organization_id": organization_id}
            return 1

        if "INSERT INTO deltallm_usertable" in query:
            if len(params) == 8:
                user_id, user_email, user_role, models, team_id, max_budget, rpm_limit, tpm_limit = params
            else:
                user_id, user_email, user_role, team_id = params
                models, max_budget, rpm_limit, tpm_limit = [], None, None, None
            self.users[str(user_id)] = {
                "user_id": user_id,
                "user_email": user_email,
                "user_role": user_role,
                "models": models,
                "team_id": team_id,
                "max_budget": max_budget,
                "rpm_limit": rpm_limit,
                "tpm_limit": tpm_limit,
                "spend": 0.0,
                "blocked": False,
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
            }
            return 1
        return 1

    async def query_raw(self, query: str, *params):
        if "SELECT team_id, team_alias, organization_id" in query and "FROM deltallm_teamtable" in query:
            row = self.teams.get(str(params[0]))
            return [row] if row else []
        if "SELECT organization_id FROM deltallm_teamtable WHERE team_id = $1" in query:
            row = self.teams.get(str(params[0]))
            return [{"organization_id": row["organization_id"]}] if row else []
        if "SELECT user_id, user_email, user_role" in query and "FROM deltallm_usertable" in query:
            return list(self.users.values())
        return []


@pytest.mark.asyncio
async def test_create_user_profile_type_alias_is_normalized(client, test_app):
    fake_db = FakeDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    response = await client.post(
        "/ui/api/users",
        headers=headers,
        json={"user_id": "user-1", "user_role": "admin"},
    )

    assert response.status_code == 200
    assert response.json()["user_role"] == "team_admin"


@pytest.mark.asyncio
async def test_create_user_profile_type_invalid_rejected(client, test_app):
    fake_db = FakeDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    response = await client.post(
        "/ui/api/users",
        headers=headers,
        json={"user_id": "user-2", "user_role": "platform_admin"},
    )

    assert response.status_code == 400
    assert "user_role must be one of" in response.text


@pytest.mark.asyncio
async def test_add_team_member_profile_type_alias_is_normalized(client, test_app):
    fake_db = FakeDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    org = await client.post("/ui/api/organizations", headers=headers, json={"organization_id": "org-1"})
    assert org.status_code == 200
    team = await client.post(
        "/ui/api/teams",
        headers=headers,
        json={"team_id": "team-1", "organization_id": "org-1"},
    )
    assert team.status_code == 200

    response = await client.post(
        "/ui/api/teams/team-1/members",
        headers=headers,
        json={"user_id": "member-1", "user_role": "user"},
    )

    assert response.status_code == 200
    assert response.json()["user_role"] == "internal_user"
