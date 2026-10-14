from __future__ import annotations

from typing import Any

import pytest

from src.api.admin.endpoints import audit as audit_endpoints
from src.api.admin.endpoints.common import AuthScope
from src.audit.categories import audit_action_category, audit_category_clause, known_audit_actions


@pytest.fixture(autouse=True)
def _reset_event_id_type(monkeypatch):
    monkeypatch.setattr(audit_endpoints, "_EVENT_ID_IS_UUID", None)


def _event_calls(fake_db):
    return [call for call in fake_db.calls if "information_schema" not in call[0]]


class FakeAuditDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def query_raw(self, query: str, *params):
        self.calls.append((query, params))
        normalized = " ".join(query.lower().split())
        if "count(*) as total from deltallm_auditevent" in normalized:
            return [{"total": 2}]
        if "from deltallm_auditpayload" in normalized:
            return [{"payload_id": "pl-1", "event_id": "evt-1", "kind": "request", "content_json": {"foo": "bar"}}]
        if "limit 1" in normalized and "from deltallm_auditevent" in normalized:
            if params[-1] == "missing":
                return []
            return [{"event_id": "evt-1", "action": "AUTH_INTERNAL_LOGIN", "organization_id": "org-1"}]
        if "from deltallm_auditevent" in normalized:
            return [
                {
                    "event_id": "evt-1",
                    "occurred_at": "2026-03-01T00:00:00+00:00",
                    "organization_id": "org-1",
                    "actor_type": "platform_account",
                    "actor_id": "acc-1",
                    "api_key": None,
                    "action": "AUTH_INTERNAL_LOGIN",
                    "resource_type": "session",
                    "resource_id": None,
                    "request_id": "req-1",
                    "correlation_id": "req-1",
                    "status": "success",
                    "metadata": {"route": "/auth/internal/login"},
                    "content_stored": False,
                }
            ]
        return []


@pytest.mark.asyncio
async def test_audit_events_list(client, test_app):
    fake_db = FakeAuditDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.settings.master_key = "mk-test"

    response = await client.get("/ui/api/audit/events", headers={"Authorization": "Bearer mk-test"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total"] == 2
    assert payload["events"][0]["event_id"] == "evt-1"


@pytest.mark.asyncio
async def test_audit_event_detail_includes_payloads(client, test_app):
    fake_db = FakeAuditDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.settings.master_key = "mk-test"

    response = await client.get("/ui/api/audit/events/evt-1", headers={"Authorization": "Bearer mk-test"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["event_id"] == "evt-1"
    assert payload["payloads"][0]["payload_id"] == "pl-1"
    event_query, event_params = _event_calls(fake_db)[0]
    assert "event_id = $" in event_query
    assert "::uuid" not in event_query
    assert event_params[-1] == "evt-1"
    payload_query, payload_params = _event_calls(fake_db)[1]
    assert "where event_id = $1" in " ".join(payload_query.lower().split())
    assert "::uuid" not in payload_query
    assert payload_params == ("evt-1",)


@pytest.mark.asyncio
async def test_audit_event_detail_missing_event_returns_404(client, test_app):
    fake_db = FakeAuditDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.settings.master_key = "mk-test"

    response = await client.get("/ui/api/audit/events/missing", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 404
    assert len(_event_calls(fake_db)) == 1
    event_query, event_params = _event_calls(fake_db)[0]
    assert "event_id = $" in event_query
    assert "::uuid" not in event_query
    assert event_params[-1] == "missing"


@pytest.mark.asyncio
async def test_audit_timeline_filters_by_request_id(client, test_app):
    fake_db = FakeAuditDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.settings.master_key = "mk-test"

    response = await client.get("/ui/api/audit/timeline?request_id=req-1", headers={"Authorization": "Bearer mk-test"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["events"][0]["request_id"] == "req-1"


@pytest.mark.asyncio
async def test_audit_export_formats(client, test_app):
    fake_db = FakeAuditDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.settings.master_key = "mk-test"

    csv_response = await client.get("/ui/api/audit/export?format=csv", headers={"Authorization": "Bearer mk-test"})
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "event_id" in csv_response.text

    jsonl_response = await client.get("/ui/api/audit/export?format=jsonl", headers={"Authorization": "Bearer mk-test"})
    assert jsonl_response.status_code == 200
    assert jsonl_response.headers["content-type"].startswith("application/x-ndjson")
    assert "evt-1" in jsonl_response.text


@pytest.mark.asyncio
async def test_audit_events_apply_org_scope_for_non_platform(client, test_app, monkeypatch):
    fake_db = FakeAuditDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.settings.master_key = "mk-test"

    monkeypatch.setattr(
        "src.api.admin.endpoints.audit.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=["org-1"],
            team_ids=[],
        ),
    )
    response = await client.get("/ui/api/audit/events", headers={"Authorization": "Bearer mk-test"})
    assert response.status_code == 200
    query, params = fake_db.calls[0]
    assert "organization_id IN (" in query
    assert "org-1" in params


class UuidAuditDB(FakeAuditDB):
    async def query_raw(self, query: str, *params):
        if "information_schema.columns" in query:
            self.calls.append((query, params))
            return [{"data_type": "uuid"}]
        return await super().query_raw(query, *params)


@pytest.mark.asyncio
async def test_audit_event_detail_uses_uuid_key_when_column_is_uuid(client, test_app):
    fake_db = UuidAuditDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.settings.master_key = "mk-test"
    event_id = "0d3c4fa1-7b1e-4c2a-9f00-a1b2c3d4e5f6"

    response = await client.get(f"/ui/api/audit/events/{event_id}", headers={"Authorization": "Bearer mk-test"})
    assert response.status_code == 200
    event_query, _ = _event_calls(fake_db)[0]
    assert "event_id = $1::uuid" in event_query

    response = await client.get("/ui/api/audit/events/not-a-uuid", headers={"Authorization": "Bearer mk-test"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_audit_event_detail_can_skip_payload_content(client, test_app):
    fake_db = FakeAuditDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.settings.master_key = "mk-test"

    response = await client.get(
        "/ui/api/audit/events/evt-1?include_payloads=false", headers={"Authorization": "Bearer mk-test"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert "payloads" not in payload
    assert payload["payload_summary"]["count"] == 1
    payload_query, _ = _event_calls(fake_db)[1]
    assert "content_json" not in payload_query


@pytest.mark.asyncio
async def test_audit_events_cursor_mode_skips_count_and_filters(client, test_app):
    fake_db = FakeAuditDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.settings.master_key = "mk-test"

    response = await client.get(
        "/ui/api/audit/events",
        params={
            "count": "false",
            "category": "auth",
            "since": "2026-10-14T09:00:00Z",
            "resource_type": "session",
            "ip": "10.0.4.17",
            "search": "req-1",
            "limit": 1,
        },
        headers={"Authorization": "Bearer mk-test"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert "total" not in payload["pagination"]
    assert payload["pagination"]["has_more"] is False
    assert not any("count(*)" in query.lower() for query, _ in fake_db.calls)
    query, params = fake_db.calls[-1]
    assert "starts_with(action, 'AUTH_')" in query
    assert "resource_type = $" in query and "ip = $" in query
    assert "event_id::text = $" in query and "request_id = $" in query
    assert params[-1] == 2  # one extra row tells whether more exist


@pytest.mark.asyncio
async def test_audit_events_reject_unknown_category_and_bad_cursor(client, test_app):
    fake_db = FakeAuditDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.settings.master_key = "mk-test"

    response = await client.get("/ui/api/audit/events?category=everything", headers={"Authorization": "Bearer mk-test"})
    assert response.status_code == 422
    response = await client.get("/ui/api/audit/events?cursor=garbage", headers={"Authorization": "Bearer mk-test"})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_audit_actions_list_needs_no_database(client, test_app):
    test_app.state.settings.master_key = "mk-test"
    response = await client.get("/ui/api/audit/actions", headers={"Authorization": "Bearer mk-test"})
    assert response.status_code == 200
    actions = {item["action"]: item["category"] for item in response.json()["actions"]}
    assert actions["ADMIN_MODEL_UPDATE"] == "admin"
    assert actions["CHAT_COMPLETION_REQUEST"] == "api"


def test_audit_categories_cover_every_prefix():
    assert audit_action_category("ADMIN_KEY_REVOKE") == "admin"
    assert audit_action_category("MANAGED_ASSET_ACCESS_UPDATE") == "admin"
    assert audit_action_category("AUTH_MFA_VERIFY") == "auth"
    assert audit_action_category("MCP_TOOL_CALL") == "mcp"
    assert audit_action_category("GLOBAL_SPEND_READ") == "spend"
    assert audit_action_category("EXTERNAL_AUTH_DENIAL") == "system"
    assert audit_action_category("BATCH_WEBHOOK_FAILED") == "system"
    assert audit_action_category("EMBEDDING_REQUEST") == "api"
    assert audit_category_clause("api").startswith("NOT (")
    assert {item["category"] for item in known_audit_actions()} == {"admin", "auth", "api", "mcp", "spend", "system"}


class OrgListDB(FakeAuditDB):
    async def query_raw(self, query: str, *params):
        if "from deltallm_organizationtable" in " ".join(query.lower().split()):
            self.calls.append((query, params))
            return [{"organization_id": "org-1", "organization_name": "Acme Corp"}]
        return await super().query_raw(query, *params)


@pytest.mark.asyncio
async def test_audit_organizations_only_lists_orgs_the_caller_can_audit(client, test_app, monkeypatch):
    fake_db = OrgListDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.settings.master_key = "mk-test"
    scopes = iter([
        AuthScope(is_platform_admin=False, org_ids=["org-1"], team_ids=[]),
        AuthScope(is_platform_admin=False, org_ids=[], team_ids=[]),
    ])
    monkeypatch.setattr(
        "src.api.admin.endpoints.audit.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: next(scopes),
    )

    response = await client.get("/ui/api/audit/organizations?search=acme", headers={"Authorization": "Bearer mk-test"})
    assert response.status_code == 200
    assert response.json() == {"organizations": [{"organization_id": "org-1", "organization_name": "Acme Corp"}]}
    query, params = fake_db.calls[0]
    assert "organization_id = ANY($1::text[])" in query
    assert params == (["org-1"], "%acme%", 8)

    response = await client.get("/ui/api/audit/organizations", headers={"Authorization": "Bearer mk-test"})
    assert response.json() == {"organizations": []}
    assert len(fake_db.calls) == 1


@pytest.mark.asyncio
async def test_audit_related_events_stay_in_scope(client, test_app, monkeypatch):
    class SignInDB(FakeAuditDB):
        async def query_raw(self, query: str, *params):
            if "limit 1" in " ".join(query.lower().split()) and "from deltallm_auditevent" in query.lower():
                self.calls.append((query, params))
                return [{
                    "event_id": "evt-1",
                    "action": "AUTH_INTERNAL_LOGIN",
                    "organization_id": "org-1",
                    "actor_id": "acc-1",
                    "request_id": "req-1",
                }]
            return await super().query_raw(query, *params)

    fake_db = SignInDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    test_app.state.settings.master_key = "mk-test"
    monkeypatch.setattr(
        "src.api.admin.endpoints.audit.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=["org-1"],
            team_ids=[],
        ),
    )

    response = await client.get("/ui/api/audit/events/evt-1/related", headers={"Authorization": "Bearer mk-test"})
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"request", "resource", "actor_signins"}
    related_calls = [call for call in _event_calls(fake_db)[1:]]
    # The loaded event has a request ID and is a sign-in: one request trail and one sign-in lookup.
    assert len(related_calls) == 2
    for query, params in related_calls:
        assert "organization_id IN (" in query
        assert "org-1" in params
    assert "req-1" in related_calls[0][1]
