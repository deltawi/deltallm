from __future__ import annotations

from datetime import UTC, datetime

import pytest


class FakeMemberCandidateDB:
    def __init__(self) -> None:
        now = datetime.now(tz=UTC)
        self.accounts = [
            {
                "account_id": "acct-a",
                "email": "alice@example.com",
                "role": "org_user",
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "account_id": "acct-b",
                "email": "bob@example.com",
                "role": "org_user",
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            },
            {
                "account_id": "acct-c",
                "email": "carol@example.com",
                "role": "org_user",
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            },
        ]
        self.org_memberships = {
            "acct-a": "org-1",
            "acct-b": "org-1",
            "acct-c": "org-2",
        }
        self.team_memberships = {
            ("acct-a", "team-1"): "team_viewer",
        }

    async def execute_raw(self, query: str, *params):
        del query, params
        return 1

    async def query_raw(self, query: str, *params):
        if "FROM deltallm_teamtable" in query and "WHERE team_id = $1" in query:
            if params[0] == "team-1":
                return [
                    {
                        "team_id": "team-1",
                        "team_alias": "Engineering",
                        "organization_id": "org-1",
                        "max_budget": None,
                        "spend": 0,
                        "models": [],
                        "rpm_limit": None,
                        "tpm_limit": None,
                        "blocked": False,
                        "created_at": datetime.now(tz=UTC),
                        "updated_at": datetime.now(tz=UTC),
                    }
                ]
            return []

        if "FROM deltallm_platformaccount" in query and "lower(email) = lower($1)" in query:
            search = str(params[0]).strip().lower()
            limit = int(params[-1])
            matched = [
                row
                for row in self.accounts
                if str(row["email"]).lower() == search or str(row["account_id"]).lower() == search
            ]
            return matched[:limit]

        if "FROM deltallm_platformaccount pa" in query and "JOIN deltallm_organizationmembership om" in query:
            organization_id = str(params[0])
            team_id = str(params[1])
            like_search = str(params[2]).lower() if len(params) == 4 else None
            limit = int(params[-1])

            rows = []
            for account in self.accounts:
                acct_id = str(account["account_id"])
                if self.org_memberships.get(acct_id) != organization_id:
                    continue
                if like_search:
                    needle = like_search.strip("%")
                    email = str(account["email"]).lower()
                    if needle not in email and needle not in acct_id.lower():
                        continue
                team_role = self.team_memberships.get((acct_id, team_id))
                rows.append(
                    {
                        **account,
                        "organization_role": "org_member",
                        "team_membership_id": f"tm-{acct_id}" if team_role else None,
                        "team_role": team_role,
                        "already_member": team_role is not None,
                    }
                )
            return rows[:limit]

        return []


@pytest.mark.asyncio
async def test_organization_member_candidates_require_exact_match(client, test_app):
    test_app.state.prisma_manager = type("Prisma", (), {"client": FakeMemberCandidateDB()})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    empty = await client.get(
        "/ui/api/organizations/org-1/member-candidates",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert empty.status_code == 200
    assert empty.json() == []

    partial = await client.get(
        "/ui/api/organizations/org-1/member-candidates?search=alice",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert partial.status_code == 200
    assert partial.json() == []

    exact = await client.get(
        "/ui/api/organizations/org-1/member-candidates?search=ALICE@EXAMPLE.COM",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert exact.status_code == 200
    payload = exact.json()
    assert len(payload) == 1
    assert payload[0]["account_id"] == "acct-a"


@pytest.mark.asyncio
async def test_team_member_candidates_are_scoped_to_team_organization(client, test_app):
    test_app.state.prisma_manager = type("Prisma", (), {"client": FakeMemberCandidateDB()})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.get(
        "/ui/api/teams/team-1/member-candidates",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert response.status_code == 200
    payload = response.json()
    account_ids = {row["account_id"] for row in payload}
    assert account_ids == {"acct-a", "acct-b"}

    alice = next(row for row in payload if row["account_id"] == "acct-a")
    bob = next(row for row in payload if row["account_id"] == "acct-b")
    assert alice["already_member"] is True
    assert bob["already_member"] is False
