from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.api.admin.endpoints.common import AuthScope
from src.auth.roles import Permission
from src.models.platform_auth import PlatformAuthContext
from src.services.ui_authorization import build_batch_create_session_capabilities


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
                "created_by_organization_id": None,
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
        if "COUNT(*) FILTER (WHERE status IN ('in_progress', 'finalizing')) AS in_progress" in query:
            return [{"total": 1, "queued": 0, "in_progress": 1, "completed": 0, "failed": 0, "cancelled": 0}]
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
@pytest.mark.parametrize("enabled", [False, True])
async def test_batch_feature_status_returns_embeddings_batch_flag(client, test_app, enabled):
    setattr(test_app.state.settings, "master_key", "mk-test")
    setattr(test_app.state.app_config.general_settings, "embeddings_batch_enabled", enabled)

    response = await client.get("/ui/api/batches/feature-status", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    assert response.json() == {"embeddings_batch_enabled": enabled}


@pytest.mark.asyncio
async def test_batch_feature_status_requires_batch_page_permission(client, test_app):
    class StubIdentityService:
        async def get_context_for_session(self, token: str):
            if token != "session-token":
                return None
            return PlatformAuthContext(
                account_id="acct-3",
                email="viewer@example.com",
                role="org_user",
                permissions=[],
                organization_memberships=[],
                team_memberships=[{"team_id": "team-1", "role": "team_viewer"}],
                mfa_enabled=False,
                mfa_verified=False,
                force_password_change=False,
            )

    test_app.state.platform_identity_service = StubIdentityService()

    response = await client.get("/ui/api/batches/feature-status", cookies={"deltallm_session": "session-token"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient permissions"


def test_build_batch_create_session_capabilities_disables_admin_actions_when_runtime_unavailable() -> None:
    scope = AuthScope(
        is_platform_admin=False,
        team_ids=["team-1"],
        team_permissions_by_id={"team-1": {Permission.KEY_UPDATE}},
    )

    capabilities = build_batch_create_session_capabilities(
        scope,
        {
            "status": "failed_retryable",
            "created_by_team_id": "team-1",
            "organization_id": "org-1",
        },
        admin_actions_enabled=False,
    )

    assert capabilities == {"view": True, "retry": False, "expire": False}


def test_build_batch_create_session_capabilities_allows_actions_when_runtime_available() -> None:
    scope = AuthScope(
        is_platform_admin=False,
        team_ids=["team-1"],
        team_permissions_by_id={"team-1": {Permission.KEY_UPDATE}},
    )

    capabilities = build_batch_create_session_capabilities(
        scope,
        {
            "status": "failed_retryable",
            "created_by_team_id": "team-1",
            "organization_id": "org-1",
        },
    )

    assert capabilities == {"view": True, "retry": True, "expire": True}


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
    assert payload["data"][0]["capabilities"] == {
        "view": True,
        "cancel": False,
        "retry_finalization": False,
        "requeue_stale": False,
        "mark_failed": False,
    }


@pytest.mark.asyncio
async def test_list_batches_exposes_repair_capabilities_for_updating_scope(client, test_app, monkeypatch):
    fake_db = FakeAuthorizationDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=[],
            team_ids=["team-1"],
            team_permissions_by_id={"team-1": {Permission.TEAM_READ, Permission.KEY_READ, Permission.KEY_UPDATE}},
        ),
    )

    response = await client.get("/ui/api/batches", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    assert response.json()["data"][0]["capabilities"] == {
        "view": True,
        "cancel": True,
        "retry_finalization": False,
        "requeue_stale": True,
        "mark_failed": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_filter", "expected_batch_id"),
    [
        ("queued", "batch-queued"),
        ("in_progress", "batch-in-progress"),
    ],
)
async def test_list_batches_status_filter_uses_enum_query(client, test_app, monkeypatch, status_filter, expected_batch_id):
    class FilteredBatchDB(FakeAuthorizationDB):
        async def query_raw(self, query: str, *params):
            if 'COUNT(*) AS total FROM deltallm_batch_job j' in query:
                if 'j.status = $' in query:
                    assert '::"DeltaLLM_BatchJobStatus"' in query
                    assert "j.status::text =" not in query
                    return [{"total": 1 if status_filter in params else 0}]
                return [{"total": len(self.batches)}]
            if "FROM deltallm_batch_job j" in query and "LEFT JOIN deltallm_teamtable t" in query:
                if "WHERE j.batch_id = $1" in query:
                    row = self.batches.get(str(params[0]))
                    return [row] if row else []
                if 'j.status = $' in query:
                    assert '::"DeltaLLM_BatchJobStatus"' in query
                    assert "j.status::text =" not in query
                    return [{
                        **self.batches["batch-1"],
                        "batch_id": expected_batch_id,
                        "status": status_filter,
                        "in_progress_items": 0 if status_filter == "queued" else self.batches["batch-1"]["in_progress_items"],
                        "completed_items": 0 if status_filter == "queued" else self.batches["batch-1"]["completed_items"],
                    }] if status_filter in params else []
                return [self.batches["batch-1"]]
            return await super().query_raw(query, *params)

    fake_db = FilteredBatchDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=[],
            team_ids=["team-1"],
            team_permissions_by_id={"team-1": {Permission.TEAM_READ, Permission.KEY_READ}},
        ),
    )

    response = await client.get(f"/ui/api/batches?status={status_filter}", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total"] == 1
    assert payload["data"][0]["batch_id"] == expected_batch_id
    assert payload["data"][0]["status"] == status_filter


@pytest.mark.asyncio
async def test_list_batches_includes_org_owned_batches_when_scope_has_team_and_org_memberships(client, test_app, monkeypatch):
    class MixedScopeBatchDB(FakeAuthorizationDB):
        def __init__(self) -> None:
            super().__init__()
            now = datetime.now(tz=UTC)
            self.batches["batch-org"] = {
                "batch_id": "batch-org",
                "endpoint": "/v1/embeddings",
                "status": "completed",
                "model": "gpt-4o-mini",
                "execution_mode": "serial",
                "metadata": {},
                "total_items": 2,
                "completed_items": 2,
                "failed_items": 0,
                "cancelled_items": 0,
                "in_progress_items": 0,
                "created_by_api_key": "sk-batch-org-1234",
                "created_by_team_id": None,
                "created_by_organization_id": "org-1",
                "organization_id": "org-1",
                "team_alias": None,
                "created_at": now,
                "started_at": now,
                "completed_at": now,
                "cancel_requested_at": None,
                "expires_at": None,
                "total_cost": 0.5,
                "total_provider_cost": 0.4,
                "total_billed_cost": 0.5,
            }

        async def query_raw(self, query: str, *params):
            if "COUNT(*) AS total FROM deltallm_batch_job j" in query:
                return [{"total": len(self.batches)}]
            if "FROM deltallm_batch_job j" in query and "LEFT JOIN deltallm_teamtable t" in query:
                if "WHERE j.batch_id = $1" in query:
                    row = self.batches.get(str(params[0]))
                    return [row] if row else []
                return list(self.batches.values())
            return await super().query_raw(query, *params)

    fake_db = MixedScopeBatchDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=["org-1"],
            team_ids=["team-1"],
            org_permissions_by_id={"org-1": {Permission.KEY_READ}},
            team_permissions_by_id={"team-1": {Permission.KEY_READ}},
        ),
    )

    response = await client.get("/ui/api/batches", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total"] == 2
    assert {item["batch_id"] for item in payload["data"]} == {"batch-1", "batch-org"}


@pytest.mark.asyncio
async def test_batch_summary_counts_finalizing_as_in_progress(client, test_app, monkeypatch):
    fake_db = FakeAuthorizationDB()
    fake_db.batches["batch-1"]["status"] = "finalizing"
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=[],
            team_ids=["team-1"],
            team_permissions_by_id={"team-1": {Permission.TEAM_READ, Permission.KEY_READ}},
        ),
    )

    response = await client.get("/ui/api/batches/summary", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    assert response.json()["in_progress"] == 1


@pytest.mark.asyncio
async def test_batch_summary_includes_org_owned_batches_when_scope_has_team_and_org_memberships(client, test_app, monkeypatch):
    class MixedScopeBatchDB(FakeAuthorizationDB):
        def __init__(self) -> None:
            super().__init__()
            self.batches["batch-org"] = {
                **self.batches["batch-1"],
                "batch_id": "batch-org",
                "status": "completed",
                "completed_items": 2,
                "in_progress_items": 0,
                "failed_items": 0,
                "cancelled_items": 0,
                "created_by_team_id": None,
                "created_by_organization_id": "org-1",
                "team_alias": None,
            }

        async def query_raw(self, query: str, *params):
            if "COUNT(*) FILTER (WHERE status IN ('in_progress', 'finalizing')) AS in_progress" in query:
                completed = sum(1 for row in self.batches.values() if row["status"] == "completed")
                failed = sum(1 for row in self.batches.values() if row["status"] == "failed")
                cancelled = sum(1 for row in self.batches.values() if row["status"] == "cancelled")
                queued = sum(1 for row in self.batches.values() if row["status"] == "queued")
                in_progress = sum(1 for row in self.batches.values() if row["status"] in {"in_progress", "finalizing"})
                return [{
                    "total": len(self.batches),
                    "queued": queued,
                    "in_progress": in_progress,
                    "completed": completed,
                    "failed": failed,
                    "cancelled": cancelled,
                }]
            return await super().query_raw(query, *params)

    fake_db = MixedScopeBatchDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batches.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=["org-1"],
            team_ids=["team-1"],
            org_permissions_by_id={"org-1": {Permission.KEY_READ}},
            team_permissions_by_id={"team-1": {Permission.KEY_READ}},
        ),
    )

    response = await client.get("/ui/api/batches/summary", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    assert response.json() == {
        "total": 2,
        "queued": 0,
        "in_progress": 1,
        "completed": 1,
        "failed": 0,
        "cancelled": 0,
    }


@pytest.mark.asyncio
async def test_list_batch_create_sessions_exposes_session_capabilities_for_updating_scope(client, test_app, monkeypatch):
    class SessionDB(FakeAuthorizationDB):
        def __init__(self) -> None:
            super().__init__()
            now = datetime.now(tz=UTC)
            self.sessions = [
                {
                    "session_id": "session-1",
                    "target_batch_id": "batch-1",
                    "status": "failed_retryable",
                    "endpoint": "/v1/embeddings",
                    "input_file_id": "file-1",
                    "expected_item_count": 3,
                    "inferred_model": "text-embedding-3-small",
                    "requested_service_tier": None,
                    "effective_service_tier": None,
                    "created_by_api_key": "sk-session-test-key-1234",
                    "created_by_user_id": "user-1",
                    "created_by_team_id": "team-1",
                    "created_by_organization_id": "org-1",
                    "last_error_code": "pending_limit_exceeded",
                    "last_error_message": "cap reached",
                    "promotion_attempt_count": 2,
                    "created_at": now,
                    "completed_at": None,
                    "expires_at": None,
                    "team_alias": "Team One",
                    "organization_id": "org-1",
                }
            ]

        async def query_raw(self, query: str, *params):
            if "COUNT(*) AS total" in query and "FROM deltallm_batch_create_session s" in query:
                return [{"total": len(self.sessions)}]
            if "FROM deltallm_batch_create_session s" in query and "LEFT JOIN deltallm_teamtable t" in query:
                if "WHERE s.session_id = $1" in query:
                    return [self.sessions[0]]
                return list(self.sessions)
            return await super().query_raw(query, *params)

    fake_db = SessionDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.batch_create_session_admin_service = object()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.batch_create_sessions.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=[],
            team_ids=["team-1"],
            team_permissions_by_id={"team-1": {Permission.TEAM_READ, Permission.KEY_READ, Permission.KEY_UPDATE}},
        ),
    )

    response = await client.get("/ui/api/batch-create-sessions", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total"] == 1
    assert payload["data"][0]["session_id"] == "session-1"
    assert payload["data"][0]["capabilities"] == {
        "view": True,
        "retry": True,
        "expire": True,
    }
