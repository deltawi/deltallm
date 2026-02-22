from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest


class FakeAdminDB:
    def __init__(self) -> None:
        self.organizations: dict[str, dict[str, Any]] = {}
        self.teams: dict[str, dict[str, Any]] = {}
        self.users: dict[str, dict[str, Any]] = {}

    async def execute_raw(self, query: str, *params):
        if "INSERT INTO litellm_organizationtable" in query:
            organization_id, organization_name, max_budget, rpm_limit, tpm_limit = params
            self.organizations[organization_id] = {
                "organization_id": organization_id,
                "organization_name": organization_name,
                "max_budget": max_budget,
                "spend": 0.0,
                "rpm_limit": rpm_limit,
                "tpm_limit": tpm_limit,
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
            }
            return 1

        if "UPDATE litellm_organizationtable" in query:
            organization_name, max_budget, rpm_limit, tpm_limit, organization_id = params
            row = self.organizations[organization_id]
            row.update(
                {
                    "organization_name": organization_name,
                    "max_budget": max_budget,
                    "rpm_limit": rpm_limit,
                    "tpm_limit": tpm_limit,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
            return 1

        if "INSERT INTO litellm_teamtable" in query:
            team_id, team_alias, organization_id, max_budget, rpm_limit, tpm_limit, models = params
            self.teams[team_id] = {
                "team_id": team_id,
                "team_alias": team_alias,
                "organization_id": organization_id,
                "max_budget": max_budget,
                "spend": 0.0,
                "models": models,
                "rpm_limit": rpm_limit,
                "tpm_limit": tpm_limit,
                "blocked": False,
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
            }
            return 1

        if "UPDATE litellm_teamtable" in query:
            team_alias, organization_id, max_budget, rpm_limit, tpm_limit, models, team_id = params
            row = self.teams[team_id]
            row.update(
                {
                    "team_alias": team_alias,
                    "organization_id": organization_id,
                    "max_budget": max_budget,
                    "rpm_limit": rpm_limit,
                    "tpm_limit": tpm_limit,
                    "models": models,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
            return 1

        if "INSERT INTO litellm_usertable" in query:
            user_id, user_email, user_role, models, team_id, max_budget, rpm_limit, tpm_limit = params
            self.users[user_id] = {
                "user_id": user_id,
                "user_email": user_email,
                "user_role": user_role,
                "team_id": team_id,
                "spend": 0.0,
                "max_budget": max_budget,
                "models": models,
                "rpm_limit": rpm_limit,
                "tpm_limit": tpm_limit,
                "blocked": False,
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
            }
            return 1

        if "UPDATE litellm_usertable" in query:
            user_email, user_role, team_id, max_budget, models, rpm_limit, tpm_limit, user_id = params
            row = self.users[user_id]
            row.update(
                {
                    "user_email": user_email,
                    "user_role": user_role,
                    "team_id": team_id,
                    "max_budget": max_budget,
                    "models": models,
                    "rpm_limit": rpm_limit,
                    "tpm_limit": tpm_limit,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
            return 1

        return 1

    async def query_raw(self, query: str, *params):
        if "FROM litellm_organizationtable" in query:
            if "WHERE organization_id = $1" in query:
                row = self.organizations.get(str(params[0]))
                return [row] if row else []
            return list(self.organizations.values())

        if "FROM litellm_teamtable" in query:
            if "WHERE team_id = $1" in query:
                row = self.teams.get(str(params[0]))
                return [row] if row else []
            return list(self.teams.values())

        if "FROM litellm_usertable" in query:
            if "WHERE user_id = $1" in query:
                row = self.users.get(str(params[0]))
                return [row] if row else []
            return list(self.users.values())

        return []


@pytest.mark.asyncio
async def test_ui_create_endpoints_accept_rate_limits(client, test_app):
    fake_db = FakeAdminDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    org = await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={"organization_id": "org-1", "rpm_limit": 40, "tpm_limit": 4000},
    )
    team = await client.post(
        "/ui/api/teams",
        headers=headers,
        json={"team_id": "team-1", "organization_id": "org-1", "rpm_limit": 30, "tpm_limit": 3000},
    )
    user = await client.post(
        "/ui/api/users",
        headers=headers,
        json={"user_id": "user-1", "team_id": "team-1", "rpm_limit": 20, "tpm_limit": 2000},
    )

    assert org.status_code == 200
    assert team.status_code == 200
    assert user.status_code == 200
    assert org.json()["rpm_limit"] == 40
    assert team.json()["tpm_limit"] == 3000
    assert user.json()["rpm_limit"] == 20


@pytest.mark.asyncio
async def test_ui_update_endpoints_persist_rate_limits(client, test_app):
    fake_db = FakeAdminDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={"organization_id": "org-2", "rpm_limit": 1, "tpm_limit": 10},
    )
    await client.post(
        "/ui/api/teams",
        headers=headers,
        json={"team_id": "team-2", "organization_id": "org-2", "rpm_limit": 1, "tpm_limit": 10},
    )
    await client.post(
        "/ui/api/users",
        headers=headers,
        json={"user_id": "user-2", "team_id": "team-2", "rpm_limit": 1, "tpm_limit": 10},
    )

    org = await client.put(
        "/ui/api/organizations/org-2",
        headers=headers,
        json={"rpm_limit": 99, "tpm_limit": 9999},
    )
    team = await client.put(
        "/ui/api/teams/team-2",
        headers=headers,
        json={"rpm_limit": 88, "tpm_limit": 8888},
    )
    user = await client.put(
        "/ui/api/users/user-2",
        headers=headers,
        json={"rpm_limit": 77, "tpm_limit": 7777},
    )

    assert org.status_code == 200
    assert team.status_code == 200
    assert user.status_code == 200
    assert org.json()["rpm_limit"] == 99
    assert team.json()["tpm_limit"] == 8888
    assert user.json()["rpm_limit"] == 77
