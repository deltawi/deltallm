from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.api.admin.endpoints.common import AuthScope
from src.auth.roles import Permission
from src.models.platform_auth import PlatformAuthContext


class FakeAuthorizationDB:
    def __init__(self) -> None:
        now = datetime.now(tz=UTC)
        self.organizations = {
            "org-1": {
                "organization_id": "org-1",
                "organization_name": "Org One",
                "max_budget": None,
                "soft_budget": None,
                "spend": 0.0,
                "rpm_limit": None,
                "tpm_limit": None,
                "rph_limit": None,
                "rpd_limit": None,
                "tpd_limit": None,
                "model_rpm_limit": None,
                "model_tpm_limit": None,
                "audit_content_storage_enabled": False,
                "metadata": {},
                "created_at": now,
                "updated_at": now,
            },
        }
        self.teams = {
            "team-1": {
                "team_id": "team-1",
                "team_alias": "Team One",
                "organization_id": "org-1",
                "max_budget": None,
                "spend": 0.0,
                "rpm_limit": None,
                "tpm_limit": None,
                "rph_limit": None,
                "rpd_limit": None,
                "tpd_limit": None,
                "model_rpm_limit": None,
                "model_tpm_limit": None,
                "blocked": False,
                "self_service_keys_enabled": True,
                "self_service_max_keys_per_user": None,
                "self_service_budget_ceiling": None,
                "self_service_require_expiry": False,
                "self_service_max_expiry_days": None,
                "created_at": now,
                "updated_at": now,
                "member_count": 1,
            },
        }
        self.batches = {
            "batch-1": {
                "batch_id": "batch-1",
                "endpoint": "/v1/embeddings",
                "status": "in_progress",
                "model": "gpt-4o-mini",
                "execution_mode": "serial",
                "metadata": {},
                "total_items": 4,
                "completed_items": 1,
                "failed_items": 0,
                "cancelled_items": 0,
                "in_progress_items": 3,
                "created_by_api_key": "sk-batch-test-key-1234",
                "created_by_team_id": "team-1",
                "organization_id": "org-1",
                "team_alias": "Team One",
                "created_at": now,
                "started_at": now,
                "completed_at": None,
                "cancel_requested_at": None,
                "expires_at": None,
                "total_cost": 0.25,
                "total_provider_cost": 0.2,
                "total_billed_cost": 0.25,
            },
        }
        self.batch_items = [
            {
                "item_id": "item-1",
                "line_number": 1,
                "custom_id": "req-1",
                "status": "completed",
                "attempts": 1,
                "provider_cost": 0.1,
                "billed_cost": 0.125,
                "last_error": None,
                "request_body": {},
                "response_body": {},
                "error_body": None,
                "usage": {},
                "created_at": now,
                "started_at": now,
                "completed_at": now,
            },
        ]

    async def query_raw(self, query: str, *params):
        if "COUNT(*) AS total FROM deltallm_teamtable" in query:
            return [{"total": 1}]
        if "FROM deltallm_teamtable t" in query and "SELECT" in query:
            if "WHERE t.team_id = $1" in query:
                row = self.teams.get(str(params[0]))
                return [row] if row else []
            return [self.teams["team-1"]]
        if "FROM deltallm_organizationtable" in query:
            row = self.organizations.get(str(params[0]))
            return [row] if row else []
        if "COUNT(*) AS total FROM deltallm_batch_job j" in query:
            return [{"total": 1}]
        if "FROM deltallm_batch_job j" in query and "LEFT JOIN deltallm_teamtable t" in query:
            if "WHERE j.batch_id = $1" in query:
                row = self.batches.get(str(params[0]))
                return [row] if row else []
            return [self.batches["batch-1"]]
        if "SELECT COALESCE(SUM(provider_cost), 0) AS total_provider_cost" in query:
            batch = self.batches.get(str(params[0]))
            if not batch:
                return []
            return [{
                "total_provider_cost": batch["total_provider_cost"],
                "total_billed_cost": batch["total_billed_cost"],
            }]
        if "SELECT COUNT(*) AS total FROM deltallm_batch_item" in query:
            return [{"total": len(self.batch_items)}]
        if "FROM deltallm_batch_item" in query:
            return list(self.batch_items)
        return []


@pytest.mark.asyncio
async def test_auth_me_returns_ui_access(client, test_app):
    class StubIdentityService:
        async def get_context_for_session(self, token: str):
            if token != "session-token":
                return None
            return PlatformAuthContext(
                account_id="acct-1",
                email="user@example.com",
                role="org_user",
                permissions=[],
                organization_memberships=[{"organization_id": "org-1", "role": "org_admin"}],
                team_memberships=[{"team_id": "team-1", "role": "team_developer"}],
                mfa_enabled=False,
                mfa_verified=False,
                force_password_change=False,
            )

    test_app.state.platform_identity_service = StubIdentityService()

    response = await client.get("/auth/me", cookies={"deltallm_session": "session-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["effective_permissions"]
    assert payload["ui_access"]["dashboard"] is False
    assert payload["ui_access"]["organizations"] is True
    assert payload["ui_access"]["teams"] is True
    assert payload["ui_access"]["team_create"] is True
    assert payload["ui_access"]["keys"] is True
    assert payload["ui_access"]["batches"] is True
    assert payload["ui_access"]["mcp_servers"] is True
    assert payload["ui_access"]["mcp_approvals"] is True
    assert payload["ui_access"]["audit"] is True
    assert payload["ui_access"]["playground"] is True
    assert payload["ui_access"]["people_access"] is False
    assert payload["ui_access"]["usage"] is False


@pytest.mark.asyncio
async def test_auth_me_hides_team_create_for_team_scoped_admin_only(client, test_app):
    class StubIdentityService:
        async def get_context_for_session(self, token: str):
            if token != "session-token":
                return None
            return PlatformAuthContext(
                account_id="acct-2",
                email="team-admin@example.com",
                role="org_user",
                permissions=[],
                organization_memberships=[],
                team_memberships=[{"team_id": "team-1", "role": "team_admin"}],
                mfa_enabled=False,
                mfa_verified=False,
                force_password_change=False,
            )

    test_app.state.platform_identity_service = StubIdentityService()

    response = await client.get("/auth/me", cookies={"deltallm_session": "session-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ui_access"]["dashboard"] is False
    assert payload["ui_access"]["team_create"] is False
    assert payload["ui_access"]["teams"] is True
    assert payload["ui_access"]["organizations"] is False
    assert payload["ui_access"]["keys"] is True
    assert payload["ui_access"]["batches"] is True
    assert payload["ui_access"]["playground"] is True


@pytest.mark.asyncio
async def test_list_teams_uses_team_scope_and_returns_capabilities(client, test_app, monkeypatch):
    fake_db = FakeAuthorizationDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.teams.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=[],
            team_ids=["team-1"],
            team_permissions_by_id={"team-1": {Permission.TEAM_READ, Permission.KEY_CREATE_SELF}},
        ),
    )

    response = await client.get("/ui/api/teams", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total"] == 1
    assert payload["data"][0]["team_id"] == "team-1"
    assert payload["data"][0]["capabilities"] == {
        "view": True,
        "edit": False,
        "delete": False,
        "manage_members": False,
        "manage_assets": False,
        "manage_self_service_policy": False,
        "create_self_key": True,
    }


@pytest.mark.asyncio
async def test_get_organization_returns_capabilities(client, test_app, monkeypatch):
    fake_db = FakeAuthorizationDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.organizations.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=["org-1"],
            org_permissions_by_id={"org-1": {Permission.ORG_READ, Permission.ORG_UPDATE, Permission.TEAM_UPDATE}},
        ),
    )

    response = await client.get("/ui/api/organizations/org-1", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["organization_id"] == "org-1"
    assert payload["capabilities"] == {
        "view": True,
        "edit": True,
        "add_team": True,
        "manage_members": True,
        "manage_assets": False,
        "view_usage": False,
    }


@pytest.mark.asyncio
async def test_list_batches_keeps_developer_read_access_and_hides_cancel(client, test_app, monkeypatch):
    fake_db = FakeAuthorizationDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=[],
            team_ids=["team-1"],
            team_permissions_by_id={"team-1": {Permission.TEAM_READ, Permission.KEY_READ, Permission.KEY_CREATE_SELF}},
        ),
    )

    response = await client.get("/ui/api/batches", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total"] == 1
    assert payload["data"][0]["batch_id"] == "batch-1"
    assert payload["data"][0]["capabilities"] == {"view": True, "cancel": False}
