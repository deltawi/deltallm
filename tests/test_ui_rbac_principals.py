from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest


def _account_self_registration_metadata() -> dict:
    return {
        "self_registration": {
            "source": "self_registration",
            "registered": True,
            "default_organization_id": "org-1",
            "default_team_id": "team-1",
        }
    }


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
        self.organizations: dict[str, dict] = {
            "org-1": {
                "organization_id": "org-1",
                "organization_name": "Sandbox Org",
                "lifecycle_state": "active",
                "metadata": {
                    "source": "self_registration",
                    "self_registration_default": "organization",
                },
            }
        }
        self.teams: dict[str, dict] = {
            "team-1": {
                "team_id": "team-1",
                "team_alias": "Sandbox Team",
                "organization_id": "org-1",
                "self_service_keys_enabled": True,
                "self_service_max_keys_per_user": 2,
                "self_service_budget_ceiling": 5.0,
                "self_service_require_expiry": True,
                "self_service_max_expiry_days": 14,
                "metadata": {
                    "source": "self_registration",
                    "self_registration_default": "team",
                },
            }
        }
        self.runtime_users: dict[str, dict] = {
            "user-1": {
                "user_id": "user-1",
                "user_email": "alice@example.com",
                "team_id": "team-1",
                "max_budget": 10.0,
                "soft_budget": 8.0,
                "spend": 1.5,
                "rpm_limit": 30,
                "tpm_limit": 50000,
                "rph_limit": 200,
                "rpd_limit": 1000,
                "tpd_limit": 500000,
                "blocked": False,
                "metadata": {
                    "source": "self_registration",
                    "self_registration_default": "user",
                },
                "created_at": now,
                "updated_at": now,
            }
        }

    @asynccontextmanager
    async def tx(self):
        yield self

    async def query_raw(self, query: str, *params):
        if "FROM deltallm_organizationtable" in query and "FOR SHARE" in query:
            row = self.organizations.get(str(params[0]))
            return [row] if row else []
        if "SELECT DISTINCT organization_id" in query and "account_organizations" in query:
            account_id = str(params[0])
            organization_ids = {
                str(row["organization_id"])
                for row in self.org_memberships.values()
                if row["account_id"] == account_id
            }
            organization_ids.update(
                str(self.teams[row["team_id"]]["organization_id"])
                for row in self.team_memberships.values()
                if row["account_id"] == account_id
                and row["team_id"] in self.teams
                and self.teams[row["team_id"]].get("organization_id")
            )
            return [{"organization_id": value} for value in sorted(organization_ids)]
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
            row = self.teams.get(str(params[0]))
            return [row] if row else []
        if (
            "FROM deltallm_organizationmembership" in query
            and "WHERE account_id = $1 AND organization_id = $2" in query
        ):
            account_id = str(params[0])
            organization_id = str(params[1])
            for row in self.org_memberships.values():
                if row["account_id"] == account_id and row["organization_id"] == organization_id:
                    return [row]
            return []
        if "matched_runtime_users AS" in query:
            return self._runtime_user_rows_for_principals(params)
        if "FROM deltallm_platformaccount" in query:
            rows = []
            for account in self.accounts.values():
                row = dict(account)
                if "metadata AS account_metadata" in query:
                    row["account_metadata"] = row.pop("metadata", None)
                rows.append(row)
            return rows
        if "FROM deltallm_organizationmembership" in query and "WHERE membership_id = $1" in query:
            row = self.org_memberships.get(str(params[0]))
            return [row] if row else []
        if "FROM deltallm_organizationmembership" in query:
            rows = []
            for membership in self.org_memberships.values():
                organization = self.organizations.get(
                    str(membership.get("organization_id") or ""), {}
                )
                rows.append(
                    {
                        **membership,
                        "organization_name": organization.get("organization_name"),
                        "organization_metadata": organization.get("metadata"),
                    }
                )
            return rows
        if "FROM deltallm_teammembership tm" in query and "WHERE tm.membership_id = $1" in query:
            membership = self.team_memberships.get(str(params[0]))
            if membership is None:
                return []
            team = self.teams.get(str(membership.get("team_id") or ""), {})
            return [{**membership, "organization_id": team.get("organization_id")}]
        if "FROM deltallm_teammembership" in query:
            rows = []
            for membership in self.team_memberships.values():
                team = self.teams.get(str(membership.get("team_id") or ""), {})
                rows.append(
                    {
                        **membership,
                        "team_alias": team.get("team_alias"),
                        "organization_id": team.get("organization_id"),
                        "self_service_keys_enabled": team.get("self_service_keys_enabled"),
                        "self_service_max_keys_per_user": team.get(
                            "self_service_max_keys_per_user"
                        ),
                        "self_service_budget_ceiling": team.get("self_service_budget_ceiling"),
                        "self_service_require_expiry": team.get("self_service_require_expiry"),
                        "self_service_max_expiry_days": team.get("self_service_max_expiry_days"),
                        "team_metadata": team.get("metadata"),
                    }
                )
            return rows
        return []

    def _runtime_user_rows_for_principals(self, params: tuple[object, ...]) -> list[dict]:
        results = []
        for account_id, account_email in zip(params[::2], params[1::2], strict=False):
            matched_user = self._matched_runtime_user(str(account_id), str(account_email))
            if matched_user is None:
                continue
            team = self.teams.get(str(matched_user.get("team_id") or ""), {})
            organization = self.organizations.get(str(team.get("organization_id") or ""), {})
            results.append(
                {
                    **matched_user,
                    "matched_account_id": str(account_id),
                    "organization_id": team.get("organization_id"),
                    "team_alias": team.get("team_alias"),
                    "self_service_keys_enabled": team.get("self_service_keys_enabled"),
                    "self_service_max_keys_per_user": team.get("self_service_max_keys_per_user"),
                    "self_service_budget_ceiling": team.get("self_service_budget_ceiling"),
                    "self_service_require_expiry": team.get("self_service_require_expiry"),
                    "self_service_max_expiry_days": team.get("self_service_max_expiry_days"),
                    "team_metadata": team.get("metadata"),
                    "organization_name": organization.get("organization_name"),
                    "organization_metadata": organization.get("metadata"),
                    "user_metadata": matched_user.get("metadata"),
                }
            )
        return results

    def _matched_runtime_user(self, account_id: str, account_email: str) -> dict | None:
        by_id = self.runtime_users.get(account_id)
        if by_id is not None:
            return by_id

        for user in self.runtime_users.values():
            if str(user.get("user_email") or "") == account_email:
                return user

        account_email_lower = account_email.lower()
        lower_matches = [
            user
            for user in self.runtime_users.values()
            if str(user.get("user_email") or "").lower() == account_email_lower
            and str(user.get("user_email") or "") != account_email
        ]
        if not lower_matches:
            return None
        return sorted(lower_matches, key=lambda user: str(user.get("user_id") or ""))[0]

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
            to_delete = [
                k
                for k, v in self.team_memberships.items()
                if v["account_id"] == account_id and v["team_id"] == "team-1"
            ]
            for k in to_delete:
                del self.team_memberships[k]
            return len(to_delete)

        if "DELETE FROM deltallm_teammembership WHERE account_id = $1" in query:
            account_id = str(params[0])
            to_delete = [
                k for k, v in self.team_memberships.items() if v["account_id"] == account_id
            ]
            for k in to_delete:
                del self.team_memberships[k]
            return len(to_delete)

        if "DELETE FROM deltallm_organizationmembership WHERE account_id = $1" in query:
            account_id = str(params[0])
            to_delete = [
                k for k, v in self.org_memberships.items() if v["account_id"] == account_id
            ]
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
    assert principals[0]["runtime_user_id"] == "user-1"
    assert principals[0]["runtime_user"]["max_budget"] == 10.0
    assert principals[0]["runtime_user"]["rpm_limit"] == 30
    assert principals[0]["runtime_user"]["organization_name"] == "Sandbox Org"
    assert principals[0]["self_registration"] == {
        "is_self_registered": True,
        "seeded_user": True,
        "seeded_team": True,
        "seeded_organization": True,
        "sandbox_team_id": "team-1",
        "sandbox_organization_id": "org-1",
    }
    assert principals[0]["self_service_policy"] == {
        "team_id": "team-1",
        "team_alias": "Sandbox Team",
        "self_service_keys_enabled": True,
        "self_service_max_keys_per_user": 2,
        "self_service_budget_ceiling": 5.0,
        "self_service_require_expiry": True,
        "self_service_max_expiry_days": 14,
    }
    assert len(principals[0]["organization_memberships"]) == 1
    assert len(principals[0]["team_memberships"]) == 1
    assert principals[0]["organization_memberships"][0]["self_registration_default"] is True
    assert principals[0]["team_memberships"][0]["self_registration_default"] is True


@pytest.mark.asyncio
async def test_list_principals_uses_account_marker_for_reused_runtime_user(client, test_app):
    fake_db = FakeDB()
    fake_db.accounts["acct-1"]["metadata"] = _account_self_registration_metadata()
    fake_db.runtime_users["legacy-user"] = {
        **fake_db.runtime_users.pop("user-1"),
        "user_id": "legacy-user",
        "metadata": None,
    }
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.get("/ui/api/principals", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    principal = response.json()["data"][0]
    assert principal["runtime_user_id"] == "legacy-user"
    assert "metadata" not in principal
    assert "account_metadata" not in principal
    assert principal["self_registration"] == {
        "is_self_registered": True,
        "seeded_user": False,
        "seeded_team": True,
        "seeded_organization": True,
        "sandbox_team_id": "team-1",
        "sandbox_organization_id": "org-1",
    }


@pytest.mark.asyncio
async def test_list_principals_matches_runtime_user_email_case_insensitively(client, test_app):
    fake_db = FakeDB()
    fake_db.runtime_users["user-1"]["user_email"] = "Alice@Example.COM"
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.get("/ui/api/principals", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    principal = response.json()["data"][0]
    assert principal["runtime_user_id"] == "user-1"
    assert principal["runtime_user"]["user_email"] == "Alice@Example.COM"


@pytest.mark.asyncio
async def test_list_principals_prefers_runtime_user_id_over_email_match(client, test_app):
    fake_db = FakeDB()
    fake_db.runtime_users["acct-1"] = {
        **fake_db.runtime_users["user-1"],
        "user_id": "acct-1",
        "user_email": "other@example.com",
    }
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.get("/ui/api/principals", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    principal = response.json()["data"][0]
    assert principal["runtime_user_id"] == "acct-1"
    assert principal["runtime_user"]["user_email"] == "other@example.com"


@pytest.mark.asyncio
async def test_list_principals_prefers_exact_email_over_lowercase_fallback(client, test_app):
    fake_db = FakeDB()
    fake_db.runtime_users["legacy-user"] = {
        **fake_db.runtime_users["user-1"],
        "user_id": "legacy-user",
        "user_email": "Alice@Example.COM",
    }
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.get("/ui/api/principals", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    principal = response.json()["data"][0]
    assert principal["runtime_user_id"] == "user-1"
    assert principal["runtime_user"]["user_email"] == "alice@example.com"


def test_lower_runtime_email_lookup_migration_exists() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "prisma/migrations/20260619120000_usertable_lower_email_lookup/migration.sql"
    )
    sql = migration.read_text(encoding="utf-8")

    assert "CONCURRENTLY" not in sql
    assert 'DROP INDEX IF EXISTS "deltallm_usertable_lower_user_email_idx"' in sql
    assert 'CREATE INDEX IF NOT EXISTS "deltallm_usertable_lower_user_email_idx"' in sql
    assert '"deltallm_usertable_lower_user_email_idx"' in sql
    assert 'ON "deltallm_usertable" (lower("user_email"))' in sql
    assert 'WHERE "user_email" IS NOT NULL' in sql


@pytest.mark.asyncio
async def test_delete_org_membership_removes_org_and_team_memberships_in_org(client, test_app):
    fake_db = FakeDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.delete(
        "/ui/api/rbac/organization-memberships/om-1", headers={"Authorization": "Bearer mk-test"}
    )

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

    response = await client.delete(
        "/ui/api/rbac/team-memberships/tm-1", headers={"Authorization": "Bearer mk-test"}
    )

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert "tm-1" not in fake_db.team_memberships


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/ui/api/rbac/organization-memberships/om-1",
        "/ui/api/rbac/team-memberships/tm-1",
        "/ui/api/rbac/accounts/acct-1",
    ],
)
async def test_rbac_deletes_reject_inactive_organization(client, test_app, path):
    fake_db = FakeDB()
    fake_db.organizations["org-1"]["lifecycle_state"] = "deletion_pending"
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.delete(path, headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "organization_inactive",
        "message": "Organization administrative changes are disabled",
        "lifecycle_state": "deletion_pending",
    }
    assert "acct-1" in fake_db.accounts
    assert "om-1" in fake_db.org_memberships
    assert "tm-1" in fake_db.team_memberships


@pytest.mark.asyncio
async def test_delete_account_removes_memberships_sessions_identities(client, test_app):
    fake_db = FakeDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.delete(
        "/ui/api/rbac/accounts/acct-1", headers={"Authorization": "Bearer mk-test"}
    )

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
