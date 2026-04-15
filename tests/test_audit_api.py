from __future__ import annotations

from typing import Any

import pytest

from src.api.admin.endpoints.common import AuthScope


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
    event_query, event_params = fake_db.calls[0]
    assert "event_id = $1::uuid" in event_query or "event_id = $2::uuid" in event_query
    assert event_params[-1] == "evt-1"
    payload_query, payload_params = fake_db.calls[1]
    assert "where event_id = $1::uuid" in " ".join(payload_query.lower().split())
    assert payload_params == ("evt-1",)


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
