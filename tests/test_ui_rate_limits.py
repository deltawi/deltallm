from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest


class FakeAdminDB:
    def __init__(self) -> None:
        self.organizations: dict[str, dict[str, Any]] = {}
        self.teams: dict[str, dict[str, Any]] = {}

    async def execute_raw(self, query: str, *params):
        if "INSERT INTO deltallm_organizationtable" in query:
            (
                organization_id,
                organization_name,
                max_budget,
                soft_budget,
                budget_duration,
                budget_reset_at,
                rpm_limit,
                tpm_limit,
                rph_limit,
                rpd_limit,
                tpd_limit,
                model_rpm_limit,
                model_tpm_limit,
                audit_content_storage_enabled,
                metadata,
            ) = params[:15]
            self.organizations[organization_id] = {
                "organization_id": organization_id,
                "organization_name": organization_name,
                "max_budget": max_budget,
                "soft_budget": soft_budget,
                "budget_duration": budget_duration,
                "budget_reset_at": budget_reset_at,
                "spend": 0.0,
                "rpm_limit": rpm_limit,
                "tpm_limit": tpm_limit,
                "rph_limit": rph_limit,
                "rpd_limit": rpd_limit,
                "tpd_limit": tpd_limit,
                "model_rpm_limit": model_rpm_limit,
                "model_tpm_limit": model_tpm_limit,
                "audit_content_storage_enabled": bool(audit_content_storage_enabled) if audit_content_storage_enabled is not None else False,
                "metadata": metadata or {},
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
            }
            return 1

        if "UPDATE deltallm_organizationtable" in query:
            (
                organization_name,
                max_budget,
                soft_budget,
                budget_duration,
                budget_reset_at,
                rpm_limit,
                tpm_limit,
                rph_limit,
                rpd_limit,
                tpd_limit,
                model_rpm_limit,
                model_tpm_limit,
                audit_content_storage_enabled,
                metadata,
                organization_id,
            ) = params[:15]
            row = self.organizations[organization_id]
            row.update(
                {
                    "organization_name": organization_name,
                    "max_budget": max_budget,
                    "soft_budget": soft_budget,
                    "budget_duration": budget_duration,
                    "budget_reset_at": budget_reset_at,
                    "rpm_limit": rpm_limit,
                    "tpm_limit": tpm_limit,
                    "rph_limit": rph_limit,
                    "rpd_limit": rpd_limit,
                    "tpd_limit": tpd_limit,
                    "model_rpm_limit": model_rpm_limit,
                    "model_tpm_limit": model_tpm_limit,
                    "audit_content_storage_enabled": bool(audit_content_storage_enabled),
                    "metadata": metadata or {},
                    "updated_at": datetime.now(tz=UTC),
                }
            )
            return 1

        if "INSERT INTO deltallm_teamtable" in query:
            (
                team_id,
                team_alias,
                organization_id,
                max_budget,
                rpm_limit,
                tpm_limit,
                rph_limit,
                rpd_limit,
                tpd_limit,
                model_rpm_limit,
                model_tpm_limit,
                self_service_keys_enabled,
                self_service_max_keys_per_user,
                self_service_budget_ceiling,
                self_service_require_expiry,
                self_service_max_expiry_days,
            ) = params
            self.teams[team_id] = {
                "team_id": team_id,
                "team_alias": team_alias,
                "organization_id": organization_id,
                "max_budget": max_budget,
                "spend": 0.0,
                "rpm_limit": rpm_limit,
                "tpm_limit": tpm_limit,
                "rph_limit": rph_limit,
                "rpd_limit": rpd_limit,
                "tpd_limit": tpd_limit,
                "model_rpm_limit": model_rpm_limit,
                "model_tpm_limit": model_tpm_limit,
                "blocked": False,
                "self_service_keys_enabled": self_service_keys_enabled,
                "self_service_max_keys_per_user": self_service_max_keys_per_user,
                "self_service_budget_ceiling": self_service_budget_ceiling,
                "self_service_require_expiry": self_service_require_expiry,
                "self_service_max_expiry_days": self_service_max_expiry_days,
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
            }
            return 1

        if "UPDATE deltallm_teamtable" in query:
            (
                team_alias,
                organization_id,
                max_budget,
                rpm_limit,
                tpm_limit,
                rph_limit,
                rpd_limit,
                tpd_limit,
                model_rpm_limit,
                model_tpm_limit,
                self_service_keys_enabled,
                self_service_max_keys_per_user,
                self_service_budget_ceiling,
                self_service_require_expiry,
                self_service_max_expiry_days,
                team_id,
            ) = params
            row = self.teams[team_id]
            row.update(
                {
                    "team_alias": team_alias,
                    "organization_id": organization_id,
                    "max_budget": max_budget,
                    "rpm_limit": rpm_limit,
                    "tpm_limit": tpm_limit,
                    "rph_limit": rph_limit,
                    "rpd_limit": rpd_limit,
                    "tpd_limit": tpd_limit,
                    "model_rpm_limit": model_rpm_limit,
                    "model_tpm_limit": model_tpm_limit,
                    "self_service_keys_enabled": self_service_keys_enabled,
                    "self_service_max_keys_per_user": self_service_max_keys_per_user,
                    "self_service_budget_ceiling": self_service_budget_ceiling,
                    "self_service_require_expiry": self_service_require_expiry,
                    "self_service_max_expiry_days": self_service_max_expiry_days,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
            return 1

        return 1

    async def query_raw(self, query: str, *params):
        if "FROM deltallm_organizationtable" in query:
            if "WHERE organization_id = $1" in query:
                row = self.organizations.get(str(params[0]))
                return [row] if row else []
            return list(self.organizations.values())

        if "FROM deltallm_teamtable" in query:
            if "WHERE team_id = $1" in query:
                row = self.teams.get(str(params[0]))
                return [row] if row else []
            return list(self.teams.values())

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

    assert org.status_code == 200
    assert team.status_code == 200
    assert org.json()["rpm_limit"] == 40
    assert team.json()["tpm_limit"] == 3000


@pytest.mark.asyncio
async def test_ui_create_team_defaults_self_service_enabled(client, test_app):
    fake_db = FakeAdminDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={"organization_id": "org-ss-default"},
    )
    team = await client.post(
        "/ui/api/teams",
        headers=headers,
        json={"team_id": "team-ss-default", "organization_id": "org-ss-default"},
    )

    assert team.status_code == 200
    payload = team.json()
    assert payload["self_service_keys_enabled"] is True
    assert payload["self_service_max_keys_per_user"] is None
    assert payload["self_service_budget_ceiling"] is None
    assert payload["self_service_require_expiry"] is False
    assert payload["self_service_max_expiry_days"] is None


@pytest.mark.asyncio
async def test_ui_create_team_can_disable_self_service_explicitly(client, test_app):
    fake_db = FakeAdminDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/organizations",
        headers=headers,
        json={"organization_id": "org-ss-disabled"},
    )
    team = await client.post(
        "/ui/api/teams",
        headers=headers,
        json={
            "team_id": "team-ss-disabled",
            "organization_id": "org-ss-disabled",
            "self_service_keys_enabled": False,
        },
    )

    assert team.status_code == 200
    assert team.json()["self_service_keys_enabled"] is False


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

    assert org.status_code == 200
    assert team.status_code == 200
    assert org.json()["rpm_limit"] == 99
    assert team.json()["tpm_limit"] == 8888
