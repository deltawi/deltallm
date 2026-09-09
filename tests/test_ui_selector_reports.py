import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api.admin.endpoints.common import AuthScope
from src.auth.roles import Permission
from src.models.platform_auth import PlatformAuthContext
from src.services.audit_service import AuditService
from tests.test_selector_evaluation import fixture_payload
from tests.test_ui_rbac_scoping import FakeSpendDB


@pytest.fixture
def report_app(test_app):
    test_app.state.settings.master_key = "mk-test"
    test_app.state.prisma_manager = SimpleNamespace(client=FakeSpendDB())
    test_app.state.audit_service = AsyncMock(spec=AuditService)
    return test_app


HEADERS = {"Authorization": "Bearer mk-test"}
COSTS = "/ui/api/spend/routing-costs?start=2026-09-01T00:00:00Z&end=2026-09-02T00:00:00Z"


async def test_admin_evaluation_replays_and_audits_only_bounded_summary(
    client, report_app, monkeypatch
):
    audit = AsyncMock()
    monkeypatch.setattr(
        "src.api.admin.endpoints.selector_evaluations.emit_admin_mutation_audit", audit
    )
    payload = fixture_payload()
    payload["samples"][0]["prompt"] = "private prompt"
    response = await client.post("/ui/api/route-policy-evaluations", headers=HEADERS, json=payload)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["correct_count"] == 1
    assert "private prompt" not in response.text
    audit.assert_awaited_once()
    assert audit.call_args.kwargs["metadata"] == {
        "basis": "supplied_fixture_replay",
        "sample_count": 1,
    }
    assert "request_payload" not in audit.call_args.kwargs
    assert report_app.state.prisma_manager.client.calls == []


async def test_evaluation_bounds_and_validation_do_not_echo_fixture_content(client, report_app):
    payload = fixture_payload()
    payload["samples"][0]["expected_lane"] = "private-invalid-label"
    response = await client.post("/ui/api/route-policy-evaluations", headers=HEADERS, json=payload)
    assert response.status_code == 400
    assert "private-invalid-label" not in response.text
    response = await client.post(
        "/ui/api/route-policy-evaluations", headers=HEADERS, content="x" * 262145
    )
    assert response.status_code == 413


async def test_http_evaluation_accepts_exact_money_strings_and_rejects_float_money(
    client, report_app
):
    payload = fixture_payload()
    payload["samples"][0]["costs"] = {
        "selector_provider_cost": "0.000000000000000001",
        "answer_provider_cost": None,
    }
    response = await client.post("/ui/api/route-policy-evaluations", headers=HEADERS, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["costs"]["selector_provider_cost_exact"] == "0.000000000000000001"
    assert response.json()["costs"]["answer_provider_cost_count"] == 0
    payload["samples"][0]["costs"]["selector_provider_cost"] = 0.01
    response = await client.post("/ui/api/route-policy-evaluations", headers=HEADERS, json=payload)
    assert response.status_code == 400


async def test_evaluation_unauthenticated_is_denied_before_body_validation(client, report_app):
    response = await client.post("/ui/api/route-policy-evaluations", content="x" * 262145)
    assert response.status_code == 401


async def test_member_session_cannot_evaluate_or_select_a_broader_spend_view(client, report_app):
    class MemberIdentity:
        async def get_context_for_session(self, token):
            if token != "member-session":
                return None
            return PlatformAuthContext(
                account_id="member",
                email="member@example.com",
                role="org_user",
                permissions=[],
                organization_memberships=[{"organization_id": "org-allowed", "role": "org_member"}],
                team_memberships=[],
                mfa_enabled=False,
                mfa_verified=False,
                force_password_change=False,
            )

    report_app.state.platform_identity_service = MemberIdentity()
    client.cookies.set("deltallm_session", "member-session")
    response = await client.post(
        "/ui/api/route-policy-evaluations",
        content="x" * 262145,
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 403
    response = await client.get(COSTS + "&view=organization")
    assert response.status_code == 403
    assert not report_app.state.prisma_manager.client.calls


async def test_aggregate_cost_overflow_is_sanitized_bad_input(client, report_app):
    payload = fixture_payload()
    payload["samples"][0]["costs"] = {"selector_provider_cost": "99999999999999999999"}
    payload["samples"] *= 2
    response = await client.post("/ui/api/route-policy-evaluations", headers=HEADERS, json=payload)
    assert response.status_code == 400
    assert "99999999999999999999" not in response.text


async def test_evaluation_required_audit_failure_is_not_report_success(
    client, report_app, monkeypatch
):
    from src.services.audit_service import RequiredAuditPersistenceError

    monkeypatch.setattr(
        "src.api.admin.endpoints.selector_evaluations.emit_admin_mutation_audit",
        AsyncMock(side_effect=RequiredAuditPersistenceError()),
    )
    response = await client.post(
        "/ui/api/route-policy-evaluations", headers=HEADERS, json=fixture_payload()
    )
    assert response.status_code == 503
    assert "correct_count" not in response.text


async def test_evaluation_missing_required_audit_is_unavailable(client, report_app):
    report_app.state.audit_service = None
    response = await client.post(
        "/ui/api/route-policy-evaluations", headers=HEADERS, json=fixture_payload()
    )
    assert response.status_code == 503
    assert "correct_count" not in response.text


async def test_selector_report_openapi_matches_exact_and_optional_contracts(report_app):
    schema = report_app.openapi()
    models = schema["components"]["schemas"]
    assert models["SelectorEvaluationRequest"]["properties"]["samples"]["maxItems"] == 100
    costs = models["EvaluationCosts"]["properties"]["selector_provider_cost"]
    assert {choice["type"] for choice in costs["anyOf"]} == {"string", "null"}
    assert (
        models["RoutingCostAggregate"]["properties"]["selector_provider_cost_exact"]["type"]
        == "string"
    )
    assert set(("401", "403", "413", "503")) <= set(
        schema["paths"]["/ui/api/route-policy-evaluations"]["post"]["responses"]
    )


async def test_report_reuses_reporting_allocation_and_empty_is_explicit(client, report_app):
    response = await client.get(COSTS, headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["operation_count"] == 0 and not body["has_more"]
    assert body["summary"]["net_savings_exact"] is None
    db = report_app.state.prisma_manager.client
    assert len(db.calls) == len(db.admission_calls) == len(db.statement_timeouts) == 1
    assert report_app.state.spend_reporting_cache.load_limiter.active == 0


@pytest.mark.parametrize(
    "query",
    [
        "?start=2026-01-01&end=2026-02-15",
        "?start=2026-01-01T00:00:00Z&end=2026-01-01T00:00:00Z",
        "?start=2026-01-01T00:00:00Z&end=2026-01-02T00:00:00Z&limit=1001",
        "?start=2026-01-01T00:00:00Z&end=2026-01-02T00:00:00Z&before_operation_id=00000000-0000-0000-0000-000000000001",
    ],
)
async def test_invalid_report_range_cursor_or_page_does_no_sql(client, report_app, query):
    response = await client.get("/ui/api/spend/routing-costs" + query, headers=HEADERS)
    assert response.status_code == 422
    assert report_app.state.prisma_manager.client.calls == []


async def test_cost_report_scope_is_derived_from_authenticated_visibility(
    client, report_app, monkeypatch
):
    monkeypatch.setattr(
        "src.api.admin.endpoints.routing_costs.get_auth_scope",
        lambda *a, **k: AuthScope(
            org_permissions_by_id={"authorized-org": {Permission.SPEND_READ}},
        ),
    )
    response = await client.get(COSTS + "&model_group=arbitrary-client-group", headers=HEADERS)
    assert response.status_code == 200
    sql, params = report_app.state.prisma_manager.client.calls[0]
    assert "authorized-org" in params and "arbitrary-client-group" in params
    assert "authorized-org" not in sql and "arbitrary-client-group" not in sql
    response = await client.get(COSTS + "&view=team", headers=HEADERS)
    assert response.status_code == 403


async def test_no_reporting_scope_fails_closed(client, report_app, monkeypatch):
    monkeypatch.setattr(
        "src.api.admin.endpoints.routing_costs.get_auth_scope", lambda *a, **k: AuthScope()
    )
    response = await client.get(COSTS, headers=HEADERS)
    assert response.status_code == 403
    assert not report_app.state.prisma_manager.client.calls


async def test_cost_report_continuation_exposes_coverage_not_raw_operation_rows(client, report_app):
    class PageDB(FakeSpendDB):
        async def query_raw(self, sql, *params):
            if "WITH page" not in sql:
                return await super().query_raw(sql, *params)
            return [
                {
                    "operation_id": "00000000-0000-0000-0000-000000000001",
                    "created_at": datetime(2026, 9, 1, tzinfo=UTC),
                    "selector_state": "settled",
                    "answer_state": "pending",
                    "selector_provider_cost": "0.001",
                    "selector_customer_charge": "0.001",
                }
            ] * 2

    report_app.state.prisma_manager.client = PageDB()
    response = await client.get(COSTS + "&limit=1", headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["has_more"] and body["next_cursor"]["operation_id"]
    assert body["summary"]["selector_provider_cost_count"] == 1
    assert body["summary"]["answer_provider_cost_count"] == 0
    assert body["summary"]["pending_reconciliation_count"] == 1
    assert body["summary"]["partial"]
    assert "snapshot" not in body and "api_key" not in body


async def test_reporting_timeout_releases_shared_capacity(client, report_app):
    class SlowDB(FakeSpendDB):
        async def query_raw(self, sql, *params):
            if "WITH page" in sql:
                await asyncio.Event().wait()
            return await super().query_raw(sql, *params)

    report_app.state.prisma_manager.client = SlowDB()
    report_app.state.app_config.general_settings.spend_reporting_execution_timeout_seconds = 0.05
    response = await client.get(COSTS, headers=HEADERS)
    assert response.status_code == 503
    assert report_app.state.spend_reporting_cache.load_limiter.active == 0


async def test_reporting_cancellation_releases_shared_capacity(client, report_app):
    started = asyncio.Event()

    class BlockedDB(FakeSpendDB):
        async def query_raw(self, sql, *params):
            if "WITH page" in sql:
                started.set()
                await asyncio.Event().wait()
            return await super().query_raw(sql, *params)

    report_app.state.prisma_manager.client = BlockedDB()
    task = asyncio.create_task(client.get(COSTS, headers=HEADERS))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert report_app.state.spend_reporting_cache.load_limiter.active == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
