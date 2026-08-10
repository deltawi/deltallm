from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any

import pytest

from src.api.admin.endpoints.common import AuthScope
from src.auth.roles import Permission
from src.models.platform_auth import PlatformAuthContext


class FakeSpendDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.admission_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.statement_timeouts: list[str] = []
        self.transaction_options: list[dict[str, Any]] = []

    class _Transaction:
        def __init__(self, db: "FakeSpendDB") -> None:
            self.db = db

        async def __aenter__(self) -> "FakeSpendDB":
            return self.db

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            del exc_type, exc, traceback

    def tx(self, **options: Any) -> "FakeSpendDB._Transaction":
        self.transaction_options.append(options)
        return self._Transaction(self)

    async def query_raw(self, query: str, *params):
        if "pg_try_advisory_xact_lock" in query:
            self.admission_calls.append((query, params))
            return [{"slot": 0}]
        if "set_config('statement_timeout'" in query:
            self.statement_timeouts.append(str(params[0]))
            return [{"set_config": params[0]}]
        self.calls.append((query, params))
        if "COUNT(*) AS total_requests" in query:
            return [{
                "total_spend": 0,
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_requests": 0,
                "unique_models": 0,
            }]
        if "COUNT(*) AS total FROM" in query:
            return [{"total": 0}]
        if "GROUP BY" in query:
            return []
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_spend_feature_status_returns_cache_flag(client, test_app, enabled):
    setattr(test_app.state.settings, "master_key", "mk-test")
    setattr(test_app.state.app_config.general_settings, "cache_enabled", enabled)

    response = await client.get("/ui/api/spend/feature-status", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["cache_enabled"] is enabled
    assert payload["reporting_api_version"] == 2
    assert payload["capabilities"] == {
        "visibility_level": "platform",
        "active_view": "platform",
        "default_view": "platform",
        "available_views": ["platform"],
        "self_scoped": False,
        "allowed_dimensions": ["organization", "team", "user"],
        "request_logs": True,
        "user_identity_labels": True,
    }


@pytest.mark.asyncio
async def test_scoped_reporting_routes_stay_closed_until_v2_is_enabled(client, test_app):
    class MemberIdentityService:
        async def get_context_for_session(self, token: str):
            if token != "member-session":
                return None
            return PlatformAuthContext(
                account_id="acct-member",
                email="member@example.com",
                role="org_user",
                permissions=[],
                organization_memberships=[{"organization_id": "org-1", "role": "org_member"}],
                team_memberships=[{"team_id": "team-1", "role": "team_developer"}],
                mfa_enabled=False,
                mfa_verified=False,
                force_password_change=False,
            )

    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.platform_identity_service = MemberIdentityService()
    setattr(
        test_app.state.app_config.general_settings,
        "spend_reporting_v2_enabled",
        False,
    )

    feature_status = await client.get(
        "/ui/api/spend/feature-status",
        cookies={"deltallm_session": "member-session"},
    )
    summary = await client.get(
        "/ui/api/spend/summary",
        cookies={"deltallm_session": "member-session"},
    )

    assert feature_status.status_code == 403
    assert summary.status_code == 403
    assert fake_db.calls == []


@pytest.mark.asyncio
async def test_regular_member_usage_is_filtered_to_owned_keys(client, test_app):
    class MemberIdentityService:
        async def get_context_for_session(self, token: str):
            if token != "member-session":
                return None
            return PlatformAuthContext(
                account_id="acct-member",
                email="member@example.com",
                role="org_user",
                permissions=[],
                organization_memberships=[{"organization_id": "org-1", "role": "org_member"}],
                team_memberships=[{"team_id": "team-1", "role": "team_developer"}],
                mfa_enabled=False,
                mfa_verified=False,
                force_password_change=False,
            )

    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.platform_identity_service = MemberIdentityService()

    response = await client.get(
        "/ui/api/spend/summary",
        cookies={"deltallm_session": "member-session"},
    )

    assert response.status_code == 200
    query, params = fake_db.calls[0]
    assert "owner_account_id = $1" in query
    assert "organization_id IN ($2)" in query
    assert "team_id IN ($3)" in query
    assert params == ("acct-member", "org-1", "team-1")
    assert response.json()["capabilities"] == {
        "visibility_level": "self",
        "active_view": "self",
        "default_view": "self",
        "available_views": ["self"],
        "self_scoped": True,
        "allowed_dimensions": ["organization", "team"],
        "request_logs": False,
        "user_identity_labels": False,
    }
    assert response.json()["reporting_context"] == {
        "api_version": 2,
        "active_view": "self",
    }

    owner_report = await client.get(
        "/ui/api/spend/report?group_by=organization",
        cookies={"deltallm_session": "member-session"},
    )
    owner_query, owner_params = fake_db.calls[-1]
    model_report = await client.get(
        "/ui/api/spend/report?group_by=model&scope_type=organization&scope_id=org-1",
        cookies={"deltallm_session": "member-session"},
    )
    model_query, model_params = fake_db.calls[-1]
    user_report = await client.get(
        "/ui/api/spend/report?group_by=user",
        cookies={"deltallm_session": "member-session"},
    )
    forged_user_scope = await client.get(
        "/ui/api/spend/report?group_by=model&scope_type=user&scope_id=user-2",
        cookies={"deltallm_session": "member-session"},
    )
    logs = await client.get(
        "/ui/api/logs",
        cookies={"deltallm_session": "member-session"},
    )

    assert owner_report.status_code == 200
    assert "s.owner_account_id = $1" in owner_query
    assert "s.organization_id IN ($2)" in owner_query
    assert "s.team_id IN ($3)" in owner_query
    assert owner_params[:3] == ("acct-member", "org-1", "team-1")
    assert model_report.status_code == 200
    assert "s.owner_account_id = $1" in model_query
    assert "s.organization_id = $4" in model_query
    assert model_params[:4] == ("acct-member", "org-1", "team-1", "org-1")
    assert user_report.status_code == 403
    assert forged_user_scope.status_code == 403
    assert logs.status_code == 403


@pytest.mark.asyncio
async def test_team_admin_usage_is_filtered_to_authorized_teams(client, test_app):
    class TeamAdminIdentityService:
        async def get_context_for_session(self, token: str):
            if token != "team-admin-session":
                return None
            return PlatformAuthContext(
                account_id="acct-team-admin",
                email="team-admin@example.com",
                role="org_user",
                permissions=[],
                organization_memberships=[{"organization_id": "org-1", "role": "org_member"}],
                team_memberships=[{"team_id": "team-1", "role": "team_admin"}],
                mfa_enabled=False,
                mfa_verified=False,
                force_password_change=False,
            )

    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.platform_identity_service = TeamAdminIdentityService()

    response = await client.get(
        "/ui/api/spend/summary",
        cookies={"deltallm_session": "team-admin-session"},
    )

    assert response.status_code == 200
    query, params = fake_db.calls[0]
    assert "team_id IN ($1)" in query
    assert "owner_account_id" not in query
    assert params == ("team-1",)
    assert response.json()["capabilities"]["visibility_level"] == "team"
    assert response.json()["capabilities"]["allowed_dimensions"] == ["team", "user"]


@pytest.mark.asyncio
async def test_mixed_role_usage_views_use_separate_predicates(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    mixed_scope = AuthScope(
        account_id="acct-mixed",
        org_permissions_by_id={
            "org-1": {Permission.SPEND_READ, Permission.SPEND_READ_SELF},
        },
        team_permissions_by_id={
            "team-1": {Permission.SPEND_READ_TEAM, Permission.SPEND_READ_SELF},
        },
        effective_permissions={
            Permission.SPEND_READ,
            Permission.SPEND_READ_TEAM,
            Permission.SPEND_READ_SELF,
        },
    )
    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: mixed_scope,
    )

    organization = await client.get(
        "/ui/api/spend/summary?view=organization",
        headers={"Authorization": "Bearer mk-test"},
    )
    organization_query, organization_params = fake_db.calls[-1]
    team = await client.get(
        "/ui/api/spend/summary?view=team",
        headers={"Authorization": "Bearer mk-test"},
    )
    team_query, team_params = fake_db.calls[-1]
    self_view = await client.get(
        "/ui/api/spend/summary?view=self",
        headers={"Authorization": "Bearer mk-test"},
    )
    self_query, self_params = fake_db.calls[-1]

    assert organization.status_code == team.status_code == self_view.status_code == 200
    assert "organization_id IN ($1)" in organization_query
    assert "owner_account_id" not in organization_query
    assert organization_params == ("org-1",)
    assert "team_id IN ($1)" in team_query
    assert "organization_id IN" not in team_query
    assert team_params == ("team-1",)
    assert "owner_account_id = $1" in self_query
    assert self_params == ("acct-mixed", "org-1", "team-1")
    assert self_view.json()["capabilities"]["available_views"] == [
        "organization",
        "team",
        "self",
    ]
    assert organization.json()["reporting_context"]["active_view"] == "organization"
    assert team.json()["reporting_context"]["active_view"] == "team"
    assert self_view.json()["reporting_context"]["active_view"] == "self"


@pytest.mark.asyncio
async def test_unsafe_spend_owner_backfill_is_not_exposed(client, test_app):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/spend/owner-attribution/backfill?limit=250",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code in {404, 405}
    assert fake_db.calls == []


@pytest.mark.asyncio
async def test_spend_summary_applies_org_scope_for_non_platform(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=["org-1"],
            team_ids=[],
        ),
    )

    response = await client.get("/ui/api/spend/summary", headers={"Authorization": "Bearer mk-test"})
    assert response.status_code == 200

    query, params = fake_db.calls[0]
    assert "FROM deltallm_spendlog_events" in query
    assert "organization_id IN" in query
    assert "COUNT(DISTINCT model)" in query
    assert "org-1" in params


@pytest.mark.asyncio
async def test_spend_summary_uses_event_scope(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=["org-1"],
            team_ids=[],
        ),
    )

    response = await client.get("/ui/api/spend/summary", headers={"Authorization": "Bearer mk-test"})
    assert response.status_code == 200

    query, params = fake_db.calls[0]
    assert "FROM deltallm_spendlog_events" in query
    assert "organization_id IN" in query
    assert "team_id IN (SELECT team_id FROM deltallm_teamtable WHERE organization_id IN" not in query
    assert "org-1" in params


@pytest.mark.asyncio
async def test_spend_logs_applies_org_scope_for_non_platform(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=["org-1"],
            team_ids=[],
        ),
    )

    response = await client.get(
        "/ui/api/logs?pagination_mode=cursor",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert response.status_code == 200

    assert len(fake_db.calls) == 1
    assert any(
        "pg_try_advisory_xact_lock($1::integer, slot)" in query
        for query, _ in fake_db.admission_calls
    )
    logs_query, logs_params = fake_db.calls[0]
    assert "FROM deltallm_spendlog_events" in logs_query
    assert "organization_id IN" in logs_query
    assert "org-1" in logs_params
    assert response.json()["pagination"] == {
        "mode": "cursor",
        "limit": 100,
        "offset": 0,
        "count": 0,
        "has_more": False,
        "next_cursor": None,
    }
    assert response.json()["reporting_context"] == {
        "api_version": 2,
        "active_view": "organization",
    }


@pytest.mark.asyncio
async def test_spend_logs_use_normalized_event_columns(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
            org_ids=[],
            team_ids=[],
        ),
    )

    response = await client.get(
        "/ui/api/logs?pagination_mode=cursor",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert response.status_code == 200

    assert len(fake_db.calls) == 1
    logs_query = fake_db.calls[0]
    assert "FROM deltallm_spendlog_events" in logs_query[0]
    assert "input_tokens AS prompt_tokens" in logs_query[0]
    assert 'user_id AS "user"' in logs_query[0]
    assert "status" in logs_query[0]
    assert "http_status_code" in logs_query[0]
    assert "error_type" in logs_query[0]
    assert "ORDER BY start_time DESC, id DESC" in logs_query[0]
    assert "OFFSET" not in logs_query[0]


@pytest.mark.asyncio
async def test_spend_logs_use_one_statement_with_the_shared_deadline(
    client, test_app, monkeypatch
):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    ticks = iter([100.0, 110.0])
    monkeypatch.setattr("src.api.admin.endpoints.spend.monotonic", lambda: next(ticks))
    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
        ),
    )

    response = await client.get(
        "/ui/api/logs?pagination_mode=cursor",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    assert fake_db.statement_timeouts == ["49000ms"]
    assert len(fake_db.transaction_options) == 1
    transaction_options = fake_db.transaction_options[0]
    assert transaction_options["max_wait"].total_seconds() == 59
    assert transaction_options["timeout"].total_seconds() == 60


@pytest.mark.asyncio
async def test_spend_logs_use_stable_cursor_pagination(client, test_app, monkeypatch):
    rows = [
        {"id": "log-3", "start_time": datetime(2026, 8, 10, 12, 0, tzinfo=UTC)},
        {"id": "log-2", "start_time": datetime(2026, 8, 10, 11, 0, tzinfo=UTC)},
        {"id": "log-1", "start_time": datetime(2026, 8, 10, 10, 0, tzinfo=UTC)},
    ]

    class CursorLogsDB(FakeSpendDB):
        async def query_raw(self, query: str, *params):
            if "SELECT id, request_id" in query:
                self.calls.append((query, params))
                return rows
            return await super().query_raw(query, *params)

    fake_db = CursorLogsDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
        ),
    )

    first = await client.get(
        "/ui/api/logs?limit=2&pagination_mode=cursor",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert first.status_code == 200
    first_payload = first.json()
    assert [row["id"] for row in first_payload["logs"]] == ["log-3", "log-2"]
    assert first_payload["pagination"]["count"] == 2
    assert first_payload["pagination"]["has_more"] is True
    cursor = first_payload["pagination"]["next_cursor"]
    assert isinstance(cursor, str) and cursor
    first_query, first_params = fake_db.calls[-1]
    assert "ORDER BY start_time DESC, id DESC" in first_query
    assert "OFFSET" not in first_query
    assert first_params == (3,)

    fake_db.calls.clear()
    second = await client.get(
        f"/ui/api/logs?limit=2&pagination_mode=cursor&cursor={cursor}",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert second.status_code == 200
    second_query, second_params = fake_db.calls[0]
    assert "(start_time, id) < ($1::timestamp, $2)" in second_query
    assert "LIMIT $3" in second_query
    assert second_params == (rows[1]["start_time"], "log-2", 3)


@pytest.mark.asyncio
async def test_spend_logs_keep_total_for_legacy_offset_clients(client, test_app, monkeypatch):
    class LegacyLogsDB(FakeSpendDB):
        async def query_raw(self, query: str, *params):
            if "SELECT id, request_id" in query:
                self.calls.append((query, params))
                return [{
                    "id": "log-1",
                    "request_id": "req-1",
                    "start_time": datetime(2026, 8, 10, 10, 0, tzinfo=UTC),
                }]
            if "SELECT COUNT(*) AS total FROM" in query:
                self.calls.append((query, params))
                return [{"total": 7}]
            return await super().query_raw(query, *params)

    fake_db = LegacyLogsDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
        ),
    )

    response = await client.get(
        "/ui/api/logs?limit=2&offset=0",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    assert len(fake_db.calls) == 2
    assert fake_db.calls[0][1] == (2,)
    assert "COUNT(*) AS total" in fake_db.calls[1][0]
    assert response.json()["pagination"] == {
        "mode": "offset",
        "limit": 2,
        "offset": 0,
        "count": 1,
        "has_more": True,
        "next_cursor": None,
        "total": 7,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "pagination_mode=cursor&cursor=not-a-cursor",
        "pagination_mode=cursor&cursor=eyJzdGFydF90aW1lIjoiMjAyNi0wOC0xMFQxMTowMDowMCswMDowMCIsImlkIjoibG9nLTIifQ&offset=1",
    ],
)
async def test_spend_logs_reject_invalid_cursor_requests(client, test_app, query):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.get(
        f"/ui/api/logs?{query}",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 422
    assert fake_db.calls == []


@pytest.mark.asyncio
async def test_spend_logs_share_reporting_capacity_with_other_uncached_log_requests(
    client, test_app, monkeypatch
):
    query_started = asyncio.Event()
    release_query = asyncio.Event()

    class HeldLogsDB(FakeSpendDB):
        async def query_raw(self, query: str, *params):
            if "set_config('statement_timeout'" in query:
                return await super().query_raw(query, *params)
            if "SELECT id, request_id" in query:
                query_started.set()
                await release_query.wait()
            return await super().query_raw(query, *params)

    fake_db = HeldLogsDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    setattr(test_app.state.app_config.general_settings, "spend_reporting_max_concurrency", 1)
    setattr(test_app.state.app_config.general_settings, "spend_reporting_queue_timeout_seconds", 0.02)
    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
        ),
    )

    first = asyncio.create_task(client.get(
        "/ui/api/logs?offset=0",
        headers={"Authorization": "Bearer mk-test"},
    ))
    await query_started.wait()
    second = await client.get(
        "/ui/api/logs?offset=25",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert second.status_code == 503
    assert second.headers["retry-after"] == "2"
    assert "capacity" in second.json()["detail"].lower()
    assert test_app.state.spend_reporting_cache.load_limiter.active == 1

    release_query.set()
    assert (await first).status_code == 200
    assert test_app.state.spend_reporting_cache.load_limiter.active == 0


@pytest.mark.asyncio
async def test_spend_report_not_scoped_for_platform_admin(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
            org_ids=[],
            team_ids=[],
        ),
    )

    response = await client.get("/ui/api/spend/report", headers={"Authorization": "Bearer mk-test"})
    assert response.status_code == 200

    query, _ = fake_db.calls[0]
    assert "team_id IN (SELECT team_id FROM deltallm_teamtable WHERE organization_id IN" not in query


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("interval", "bucket_expression"),
    [
        ("day", "DATE(start_time)"),
        ("week", "DATE_TRUNC('week', start_time)::date"),
        ("month", "DATE_TRUNC('month', start_time)::date"),
    ],
)
async def test_daily_spend_report_supports_safe_time_intervals(
    client, test_app, monkeypatch, interval, bucket_expression
):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
            org_ids=[],
            team_ids=[],
        ),
    )

    response = await client.get(
        f"/ui/api/spend/report?group_by=day&interval={interval}",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    assert response.json()["interval"] == interval
    query, _ = fake_db.calls[0]
    assert f"{bucket_expression} AS group_key" in query
    assert f"GROUP BY {bucket_expression}" in query
    assert "successful_requests" in query
    assert "failed_requests" in query


@pytest.mark.asyncio
async def test_daily_spend_report_rejects_unknown_time_interval(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.get(
        "/ui/api/spend/report?group_by=day&interval=quarter",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_grouped_spend_report_applies_org_scope_for_non_platform(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=["org-1"],
            team_ids=[],
        ),
    )

    response = await client.get(
        "/ui/api/spend/report?group_by=organization&limit=5&offset=0",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert response.status_code == 200

    assert len(fake_db.calls) == 1
    query, params = fake_db.calls[0]
    assert "FROM deltallm_spendlog_events s" in query
    assert "s.organization_id IN" in query
    assert "LEFT JOIN deltallm_organizationtable o ON o.organization_id = s.organization_id" in query
    assert "LEFT JOIN deltallm_teamtable t ON t.team_id = s.team_id" not in query
    assert "SELECT COUNT(*) AS total_count FROM grouped" in query
    assert "LEFT JOIN page ON TRUE" in query
    assert "org-1" in params


@pytest.mark.asyncio
async def test_grouped_spend_report_keeps_total_when_requested_page_is_empty(
    client, test_app, monkeypatch
):
    class EmptyGroupedPageDB(FakeSpendDB):
        async def query_raw(self, query: str, *params):
            if "LEFT JOIN page ON TRUE" in query:
                return [{
                    "group_key": None,
                    "display_name": None,
                    "total_spend": None,
                    "request_count": None,
                    "total_tokens": None,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "total_count": 7,
                }]
            return await super().query_raw(query, *params)

    fake_db = EmptyGroupedPageDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
        ),
    )

    response = await client.get(
        "/ui/api/spend/report?group_by=organization&limit=5&offset=10",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == []
    assert response.json()["pagination"] == {
        "total": 7,
        "limit": 5,
        "offset": 10,
        "has_more": False,
    }


@pytest.mark.asyncio
async def test_grouped_spend_report_supports_api_key_search(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
            org_ids=[],
            team_ids=[],
        ),
    )

    response = await client.get(
        "/ui/api/spend/report?group_by=api_key&search=sk-test&limit=5&offset=0",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert response.status_code == 200

    assert len(fake_db.calls) == 1
    query, params = fake_db.calls[0]
    assert "LEFT JOIN deltallm_verificationtoken vt ON vt.token = s.api_key" in query
    assert "vt.key_name" in query
    assert "ILIKE" in query
    assert "%sk-test%" in params
    assert "ORDER BY total_spend DESC" in query


@pytest.mark.asyncio
async def test_grouped_spend_report_for_model_does_not_group_by_null_constant(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
            org_ids=[],
            team_ids=[],
        ),
    )

    response = await client.get(
        "/ui/api/spend/report?group_by=model&limit=5&offset=0",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert response.status_code == 200

    query, _ = fake_db.calls[0]
    assert "s.model AS group_key" in query
    assert "(s.model) IS NULL AS is_unassigned" in query
    assert "GROUP BY s.model" in query
    assert "__unassigned__" not in query
    assert "capabilities" not in response.json()
    assert len(fake_db.statement_timeouts) == 1
    statement_timeout_ms = int(fake_db.statement_timeouts[0].removesuffix("ms"))
    assert 58_000 <= statement_timeout_ms <= 59_000
    assert len(fake_db.transaction_options) == 1


@pytest.mark.asyncio
async def test_grouped_spend_report_serializes_null_and_literal_keys_separately(client, test_app, monkeypatch):
    class GroupRowsDB(FakeSpendDB):
        async def query_raw(self, query: str, *params):
            if "WITH grouped AS" in query:
                self.calls.append((query, params))
                return [
                    {
                        "group_key": None,
                        "is_unassigned": True,
                        "display_name": None,
                        "total_spend": 1,
                        "request_count": 1,
                        "total_tokens": 10,
                        "prompt_tokens": 6,
                        "completion_tokens": 4,
                        "total_count": 2,
                    },
                    {
                        "group_key": "__unassigned__",
                        "is_unassigned": False,
                        "display_name": None,
                        "total_spend": 2,
                        "request_count": 1,
                        "total_tokens": 20,
                        "prompt_tokens": 12,
                        "completion_tokens": 8,
                        "total_count": 2,
                    },
                ]
            return await super().query_raw(query, *params)

    fake_db = GroupRowsDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
            org_ids=[],
            team_ids=[],
        ),
    )

    response = await client.get(
        "/ui/api/spend/report?group_by=model&limit=5&offset=0",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "group_key": None,
            "is_unassigned": True,
            "display_name": None,
            "total_spend": 1,
            "request_count": 1,
            "total_tokens": 10,
            "prompt_tokens": 6,
            "completion_tokens": 4,
        },
        {
            "group_key": "__unassigned__",
            "is_unassigned": False,
            "display_name": None,
            "total_spend": 2,
            "request_count": 1,
            "total_tokens": 20,
            "prompt_tokens": 12,
            "completion_tokens": 8,
        },
    ]


@pytest.mark.asyncio
async def test_grouped_spend_report_supports_user_labels_and_token_breakdown(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
            org_ids=[],
            team_ids=[],
        ),
    )

    response = await client.get(
        "/ui/api/spend/report?group_by=user&sort_by=tokens&limit=8&offset=0",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert response.status_code == 200

    query, params = fake_db.calls[0]
    assert "LEFT JOIN deltallm_usertable u ON u.user_id = s.user_id" in query
    assert "u.user_email" in query
    assert "SUM(s.input_tokens)" in query
    assert "SUM(s.output_tokens)" in query
    assert "ORDER BY total_tokens DESC" in query
    assert params[-2:] == (8, 0)
    assert response.json()["capabilities"]["user_identity_labels"] is True


@pytest.mark.asyncio
async def test_grouped_user_report_redacts_labels_without_user_read(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=["org-1"],
            team_ids=[],
            org_permissions_by_id={"org-1": {Permission.SPEND_READ}},
        ),
    )

    response = await client.get(
        "/ui/api/spend/report?group_by=user&search=person@example.com&limit=8",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert response.status_code == 200

    query, params = fake_db.calls[0]
    assert "deltallm_usertable" not in query
    assert "u.user_email" not in query
    assert "COALESCE(s.user_id, '') ILIKE" in query
    assert "s.user_id IS NULL" in query
    assert "s.organization_id IN" in query
    assert "%person@example.com%" in params
    assert response.json()["capabilities"]["user_identity_labels"] is False


@pytest.mark.asyncio
async def test_grouped_user_report_shows_labels_with_user_read_for_all_spend_orgs(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=["org-1", "org-2"],
            team_ids=[],
            org_permissions_by_id={
                "org-1": {Permission.SPEND_READ, Permission.USER_READ},
                "org-2": {Permission.SPEND_READ, Permission.USER_READ},
            },
        ),
    )

    response = await client.get(
        "/ui/api/spend/report?group_by=user&limit=8",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert response.status_code == 200

    query, _ = fake_db.calls[0]
    assert "LEFT JOIN deltallm_usertable u ON u.user_id = s.user_id" in query
    assert "u.user_email" in query
    assert response.json()["capabilities"]["user_identity_labels"] is True


@pytest.mark.asyncio
async def test_grouped_user_report_redacts_labels_for_mixed_user_read_scope(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=["org-1", "org-2"],
            team_ids=[],
            org_permissions_by_id={
                "org-1": {Permission.SPEND_READ, Permission.USER_READ},
                "org-2": {Permission.SPEND_READ},
            },
        ),
    )

    response = await client.get(
        "/ui/api/spend/report?group_by=user&limit=8",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert response.status_code == 200
    assert "deltallm_usertable" not in fake_db.calls[0][0]
    assert response.json()["capabilities"]["user_identity_labels"] is False


@pytest.mark.asyncio
async def test_user_report_cache_separates_identity_visibility(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    user_read = {"enabled": True}

    def auth_scope(*args, **kwargs):
        del args, kwargs
        permissions = {Permission.SPEND_READ}
        if user_read["enabled"]:
            permissions.add(Permission.USER_READ)
        return AuthScope(
            is_platform_admin=False,
            org_ids=["org-1"],
            org_permissions_by_id={"org-1": permissions},
        )

    monkeypatch.setattr("src.api.admin.endpoints.spend.get_auth_scope", auth_scope)

    visible = await client.get(
        "/ui/api/spend/report?group_by=user&limit=8",
        headers={"Authorization": "Bearer mk-test"},
    )
    user_read["enabled"] = False
    hidden = await client.get(
        "/ui/api/spend/report?group_by=user&limit=8",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert visible.json()["capabilities"]["user_identity_labels"] is True
    assert hidden.json()["capabilities"]["user_identity_labels"] is False
    assert len(fake_db.calls) == 2
    assert "deltallm_usertable" in fake_db.calls[0][0]
    assert "deltallm_usertable" not in fake_db.calls[1][0]


@pytest.mark.asyncio
async def test_non_user_report_cache_is_permission_independent(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    user_read = {"enabled": True}

    def auth_scope(*args, **kwargs):
        del args, kwargs
        permissions = {Permission.SPEND_READ}
        if user_read["enabled"]:
            permissions.add(Permission.USER_READ)
        return AuthScope(
            is_platform_admin=False,
            org_ids=["org-1"],
            org_permissions_by_id={"org-1": permissions},
        )

    monkeypatch.setattr("src.api.admin.endpoints.spend.get_auth_scope", auth_scope)

    visible_caller = await client.get(
        "/ui/api/spend/report?group_by=organization&limit=8",
        headers={"Authorization": "Bearer mk-test"},
    )
    user_read["enabled"] = False
    hidden_caller = await client.get(
        "/ui/api/spend/report?group_by=organization&limit=8",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert visible_caller.status_code == 200
    assert hidden_caller.status_code == 200
    assert "capabilities" not in visible_caller.json()
    assert visible_caller.json() == hidden_caller.json()
    assert len(fake_db.calls) == 1


@pytest.mark.asyncio
async def test_spend_report_cache_reuses_identical_scoped_response(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=["org-cache"],
            team_ids=[],
            org_permissions_by_id={"org-cache": {Permission.SPEND_READ}},
        ),
    )

    first = await client.get(
        "/ui/api/spend/report?group_by=organization&start_date=2026-08-01&end_date=2026-08-10",
        headers={"Authorization": "Bearer mk-test"},
    )
    second = await client.get(
        "/ui/api/spend/report?group_by=organization&start_date=2026-08-01&end_date=2026-08-10",
        headers={"Authorization": "Bearer mk-test"},
    )
    refreshed = await client.get(
        "/ui/api/spend/report?group_by=organization&start_date=2026-08-01&end_date=2026-08-10",
        headers={"Authorization": "Bearer mk-test", "Cache-Control": "no-cache"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert refreshed.status_code == 200
    assert first.json() == second.json()
    assert refreshed.json() == first.json()
    assert len(fake_db.calls) == 2


@pytest.mark.asyncio
async def test_spend_reporting_cache_applies_live_safety_settings(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
        ),
    )

    first = await client.get(
        "/ui/api/spend/summary",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert first.status_code == 200
    cache = test_app.state.spend_reporting_cache

    setattr(test_app.state.app_config.general_settings, "spend_reporting_max_concurrency", 1)
    setattr(test_app.state.app_config.general_settings, "spend_reporting_queue_timeout_seconds", 0.25)
    setattr(test_app.state.app_config.general_settings, "spend_reporting_execution_timeout_seconds", 0.5)
    second = await client.get(
        "/ui/api/spend/summary",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert second.status_code == 200
    assert test_app.state.spend_reporting_cache is cache
    assert cache.load_limiter.limit == 1
    assert cache.load_queue_timeout_seconds == 0.25
    assert cache.load_execution_timeout_seconds == 0.5


@pytest.mark.asyncio
async def test_spend_reporting_timeout_is_retryable_and_does_not_poison_capacity(client, test_app, monkeypatch):
    query_started = asyncio.Event()
    query_cancelled = asyncio.Event()

    class StalledSpendDB(FakeSpendDB):
        def __init__(self) -> None:
            super().__init__()
            self.stall = True

        async def query_raw(self, query: str, *params):
            if "pg_try_advisory_xact_lock" in query or "set_config('statement_timeout'" in query or not self.stall:
                return await super().query_raw(query, *params)
            query_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                query_cancelled.set()

    fake_db = StalledSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    setattr(test_app.state.app_config.general_settings, "spend_reporting_max_concurrency", 1)
    setattr(test_app.state.app_config.general_settings, "spend_reporting_execution_timeout_seconds", 0.02)
    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
        ),
    )

    response = await client.get(
        "/ui/api/spend/summary?start_date=2026-08-01",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert query_started.is_set()
    assert query_cancelled.is_set()
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert "took too long" in response.json()["detail"]
    assert test_app.state.spend_reporting_cache.load_limiter.active == 0

    fake_db.stall = False
    recovered = await client.get(
        "/ui/api/spend/summary?start_date=2026-08-02",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert recovered.status_code == 200


@pytest.mark.asyncio
async def test_global_reporting_capacity_rejects_query_before_aggregation(
    client, test_app, monkeypatch
):
    class BusyReportingDB(FakeSpendDB):
        async def query_raw(self, query: str, *params):
            if "pg_try_advisory_xact_lock" in query:
                return []
            return await super().query_raw(query, *params)

    fake_db = BusyReportingDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
        ),
    )

    response = await client.get(
        "/ui/api/spend/summary?start_date=2026-08-05",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"
    assert "capacity" in response.json()["detail"].lower()
    assert fake_db.calls == []


@pytest.mark.asyncio
async def test_postgres_statement_timeout_is_mapped_to_retryable_reporting_error(client, test_app, monkeypatch):
    class StatementTimeoutSpendDB(FakeSpendDB):
        async def query_raw(self, query: str, *params):
            if "pg_try_advisory_xact_lock" in query or "set_config('statement_timeout'" in query:
                return await super().query_raw(query, *params)
            raise RuntimeError("canceling statement due to statement timeout (SQLSTATE 57014)")

    fake_db = StatementTimeoutSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
        ),
    )

    response = await client.get(
        "/ui/api/spend/summary?start_date=2026-08-03",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert test_app.state.spend_reporting_cache.load_limiter.active == 0


@pytest.mark.asyncio
async def test_reporting_query_does_not_start_after_connection_wait_exhausts_deadline(
    client, test_app, monkeypatch
):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    ticks = iter([100.0, 160.0])
    monkeypatch.setattr("src.api.admin.endpoints.spend.monotonic", lambda: next(ticks))
    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
        ),
    )

    response = await client.get(
        "/ui/api/spend/summary?start_date=2026-08-04",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert fake_db.statement_timeouts == []
    assert fake_db.calls == []
    assert test_app.state.spend_reporting_cache.load_limiter.active == 0


@pytest.mark.asyncio
async def test_time_series_cache_ignores_parameters_not_used_by_its_query(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
        ),
    )

    first = await client.get(
        "/ui/api/spend/report?group_by=day&interval=day&search=first&sort_by=spend&limit=5&offset=0",
        headers={"Authorization": "Bearer mk-test"},
    )
    second = await client.get(
        "/ui/api/spend/report?group_by=day&interval=day&search=second&sort_by=tokens&limit=100&offset=5000",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert len(fake_db.calls) == 1


@pytest.mark.asyncio
async def test_time_series_interval_remains_part_of_effective_cache_identity(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
        ),
    )

    day = await client.get(
        "/ui/api/spend/report?group_by=day&interval=day",
        headers={"Authorization": "Bearer mk-test"},
    )
    week = await client.get(
        "/ui/api/spend/report?group_by=day&interval=week",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert day.status_code == 200
    assert week.status_code == 200
    assert len(fake_db.calls) == 2


@pytest.mark.asyncio
async def test_grouped_report_cache_ignores_unused_interval(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
        ),
    )

    first = await client.get(
        "/ui/api/spend/report?group_by=organization&interval=day&limit=8",
        headers={"Authorization": "Bearer mk-test"},
    )
    second = await client.get(
        "/ui/api/spend/report?group_by=organization&interval=month&limit=8",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert len(fake_db.calls) == 1


@pytest.mark.asyncio
async def test_provider_report_cache_key_tracks_runtime_provider_overrides(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    override_state = {"value": {"vendor/model": "groq"}}

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
        ),
    )
    monkeypatch.setattr(
        "src.api.admin.endpoints.spend._legacy_cache_model_provider_overrides",
        lambda request: override_state["value"],
    )

    first = await client.get(
        "/ui/api/spend/report?group_by=provider&limit=5",
        headers={"Authorization": "Bearer mk-test"},
    )
    override_state["value"] = {"vendor/model": "fireworks"}
    second = await client.get(
        "/ui/api/spend/report?group_by=provider&limit=5",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(fake_db.calls) == 2


@pytest.mark.asyncio
async def test_concurrent_identical_reports_share_one_database_query(client, test_app, monkeypatch):
    query_started = asyncio.Event()
    release_query = asyncio.Event()

    class BlockingSpendDB(FakeSpendDB):
        async def query_raw(self, query: str, *params):
            if "pg_try_advisory_xact_lock" in query or "set_config('statement_timeout'" in query:
                return await super().query_raw(query, *params)
            query_started.set()
            await release_query.wait()
            return await super().query_raw(query, *params)

    fake_db = BlockingSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=["org-concurrent"],
            org_permissions_by_id={"org-concurrent": {Permission.SPEND_READ}},
        ),
    )

    path = "/ui/api/spend/report?group_by=organization&start_date=2026-08-01&end_date=2026-08-10"
    first = asyncio.create_task(client.get(path, headers={"Authorization": "Bearer mk-test"}))
    await query_started.wait()
    second = asyncio.create_task(client.get(path, headers={"Authorization": "Bearer mk-test"}))
    await asyncio.sleep(0)
    release_query.set()
    first_response, second_response = await asyncio.gather(first, second)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json() == second_response.json()
    assert len(fake_db.calls) == 1


@pytest.mark.asyncio
async def test_grouped_model_breakdown_narrows_existing_authorization_scope(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=["org-allowed"],
            team_ids=[],
        ),
    )

    response = await client.get(
        "/ui/api/spend/report?group_by=model&scope_type=team&scope_id=team-1&limit=8",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert response.status_code == 200

    query, params = fake_db.calls[0]
    assert "s.organization_id IN" in query
    assert "s.team_id =" in query
    assert "org-allowed" in params
    assert "team-1" in params


@pytest.mark.asyncio
async def test_grouped_model_breakdown_supports_unassigned_owner(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
            org_ids=[],
            team_ids=[],
        ),
    )

    unassigned_response = await client.get(
        "/ui/api/spend/report?group_by=model&scope_type=user&scope_unassigned=true",
        headers={"Authorization": "Bearer mk-test"},
    )
    literal_response = await client.get(
        "/ui/api/spend/report?group_by=model&scope_type=user&scope_id=__unassigned__",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert unassigned_response.status_code == 200
    assert literal_response.status_code == 200

    unassigned_query, unassigned_params = fake_db.calls[0]
    literal_query, literal_params = fake_db.calls[1]
    assert "s.user_id IS NULL" in unassigned_query
    assert "__unassigned__" not in unassigned_params
    assert "s.user_id =" in literal_query
    assert "__unassigned__" in literal_params


@pytest.mark.asyncio
async def test_grouped_spend_report_requires_complete_owner_scope(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    missing_id = await client.get(
        "/ui/api/spend/report?group_by=model&scope_type=organization",
        headers={"Authorization": "Bearer mk-test"},
    )
    missing_type = await client.get(
        "/ui/api/spend/report?group_by=model&scope_id=org-1",
        headers={"Authorization": "Bearer mk-test"},
    )
    ambiguous = await client.get(
        "/ui/api/spend/report?group_by=model&scope_type=organization&scope_id=org-1&scope_unassigned=true",
        headers={"Authorization": "Bearer mk-test"},
    )
    unassigned_without_type = await client.get(
        "/ui/api/spend/report?group_by=model&scope_unassigned=true",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert missing_id.status_code == 422
    assert missing_type.status_code == 422
    assert ambiguous.status_code == 422
    assert unassigned_without_type.status_code == 422


@pytest.mark.asyncio
async def test_grouped_spend_report_for_provider_uses_canonical_provider_grouping(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.model_registry["gpt-oss-20b"] = [
        {
            "deltallm_params": {
                "provider": "groq",
                "model": "openai/gpt-oss-20b",
                "api_key": "provider-key",
                "api_base": "https://api.groq.com/openai/v1",
            }
        }
    ]

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
            org_ids=[],
            team_ids=[],
        ),
    )

    response = await client.get(
        "/ui/api/spend/report?group_by=provider&limit=5&offset=0",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert response.status_code == 200

    query, _ = fake_db.calls[0]
    assert "LOWER(TRIM(s.provider))" in query
    assert "metadata->>'provider'" in query
    assert "s.deployment_model" in query
    assert "openai.azure.com" in query
    assert "api.groq.com" in query
    assert "openai/gpt-oss-20b" in query
    assert "THEN 'groq'" in query
    assert "<> 'cache'" in query
    assert "COALESCE(s.api_base, 'unknown')" not in query
    metadata_pos = query.index("metadata->>'provider'")
    provider_pos = query.index("NULLIF(LOWER(TRIM(s.provider))")
    cache_override_pos = query.index("<> 'cache'")
    api_base_pos = query.index("openai.azure.com")
    assert metadata_pos < provider_pos < cache_override_pos < api_base_pos


@pytest.mark.asyncio
async def test_spend_endpoints_cast_date_filters_to_timestamp(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.api.admin.endpoints.spend.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
            org_ids=[],
            team_ids=[],
        ),
    )

    start = date(2026, 2, 1).isoformat()
    end = date(2026, 2, 27).isoformat()

    summary = await client.get(
        f"/ui/api/spend/summary?start_date={start}&end_date={end}",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert summary.status_code == 200
    summary_query, _ = fake_db.calls[0]
    assert "::timestamp" in summary_query

    fake_db.calls.clear()
    report = await client.get(
        f"/ui/api/spend/report?start_date={start}&end_date={end}",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert report.status_code == 200
    report_query, _ = fake_db.calls[0]
    assert "::timestamp" in report_query

    fake_db.calls.clear()
    logs = await client.get(
        f"/ui/api/logs?start_date={start}&end_date={end}",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert logs.status_code == 200
    logs_query, _ = fake_db.calls[0]
    assert "::timestamp" in logs_query
