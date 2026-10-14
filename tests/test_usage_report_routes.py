"""Usage explorer, live Dashboard and request log routes: scope, validation and shape."""

from __future__ import annotations

from typing import Any

import pytest

from src.api.admin.endpoints.common import AuthScope
from src.api.admin.endpoints.usage_explorer import series_payload
from src.auth.roles import Permission
from tests.test_ui_rbac_scoping import FakeSpendDB

HEADERS = {"Authorization": "Bearer mk-test"}
SCOPE_PATH = "src.api.admin.usage_report_context.get_auth_scope"


def _install(test_app, monkeypatch, scope: AuthScope, db: FakeSpendDB | None = None) -> FakeSpendDB:
    fake_db = db or FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")
    for path in (SCOPE_PATH, "src.api.admin.endpoints.spend.get_auth_scope"):
        monkeypatch.setattr(
            path,
            lambda request, authorization=None, x_master_key=None, required_permission=None: scope,
        )
    return fake_db


PLATFORM = AuthScope(is_platform_admin=True)
ORGANIZATION = AuthScope(is_platform_admin=False, org_ids=["org-1"], team_ids=[])
TEAM = AuthScope(
    account_id="acct-team",
    team_permissions_by_id={"team-1": {Permission.SPEND_READ_TEAM}},
    effective_permissions={Permission.SPEND_READ_TEAM},
)


@pytest.mark.asyncio
async def test_summary_compares_with_previous_window_only_when_range_has_a_start(
    client, test_app, monkeypatch
):
    fake_db = _install(test_app, monkeypatch, PLATFORM)

    open_range = await client.get("/ui/api/spend/explorer/summary", headers=HEADERS)
    bounded = await client.get(
        "/ui/api/spend/explorer/summary?start_date=2026-10-01&end_date=2026-10-07&model=gpt-4o",
        headers=HEADERS,
    )

    assert open_range.status_code == 200
    assert open_range.json()["previous"] is None
    assert open_range.json()["current"]["request_count"] == 0
    assert bounded.status_code == 200
    payload = bounded.json()
    assert payload["previous"] is not None
    assert payload["window"] == {
        "start_date": "2026-10-01",
        "end_date": "2026-10-07",
        "previous_start_date": "2026-09-24",
        "previous_end_date": "2026-09-30",
    }
    query, params = fake_db.calls[-1]
    assert "previous_total_spend" in query
    assert "gpt-4o" in params


@pytest.mark.asyncio
async def test_series_and_breakdown_apply_the_organization_view(client, test_app, monkeypatch):
    fake_db = _install(test_app, monkeypatch, ORGANIZATION)

    series = await client.get(
        "/ui/api/spend/explorer/series?start_date=2026-10-01&split_by=provider&max_groups=3",
        headers=HEADERS,
    )
    breakdown = await client.get(
        "/ui/api/spend/explorer/breakdown?group_by=api_key&start_date=2026-10-01&search=bot",
        headers=HEADERS,
    )

    assert series.status_code == 200
    assert series.json()["groups"] == []
    assert breakdown.status_code == 200
    assert breakdown.json()["pagination"]["total"] == 0
    for query, params in fake_db.calls:
        assert "organization_id IN" in query
        assert "org-1" in params
    assert "%bot%" in fake_db.calls[-1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/ui/api/spend/explorer/breakdown?group_by=organization", 403),
        ("/ui/api/spend/explorer/summary?organization=org-2", 403),
        ("/ui/api/spend/explorer/series?split_by=api_key", 403),
        ("/ui/api/spend/explorer/summary?unassigned=provider", 422),
        ("/ui/api/spend/explorer/summary?team=t-1&unassigned=team", 422),
        ("/ui/api/spend/explorer/summary?start_date=2026-10-08&end_date=2026-10-01", 422),
        ("/ui/api/spend/explorer/series?split_by=region", 422),
    ],
)
async def test_explorer_rejects_filters_outside_the_team_view(
    client, test_app, monkeypatch, path, status
):
    fake_db = _install(test_app, monkeypatch, TEAM)

    response = await client.get(path, headers=HEADERS)

    assert response.status_code == status
    assert fake_db.calls == []


def test_series_payload_puts_other_last_and_sums_group_totals():
    rows: list[dict[str, Any]] = [
        {"bucket": "2026-10-01", "series_index": 0, "total_spend": 1, "request_count": 2},
        {
            "bucket": "2026-10-01",
            "series_index": 1,
            "group_key": "gpt-4o",
            "total_spend": 3,
            "request_count": 4,
            "failed_requests": 1,
        },
        {
            "bucket": "2026-10-02",
            "series_index": 1,
            "group_key": "gpt-4o",
            "total_spend": 2,
            "request_count": 1,
        },
    ]

    payload = series_payload(rows)

    assert [group["series_index"] for group in payload["groups"]] == [1, 0]
    assert payload["groups"][0]["total_spend"] == 5
    assert payload["groups"][0]["failed_requests"] == 1
    assert payload["groups"][1]["is_other"] is True
    assert len(payload["buckets"]) == 3


@pytest.mark.asyncio
async def test_live_hourly_returns_48_zero_filled_hours(client, test_app, monkeypatch):
    fake_db = _install(test_app, monkeypatch, PLATFORM)

    response = await client.get("/ui/api/spend/live/hourly", headers=HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["buckets"]) == 48
    assert all(bucket["request_count"] == 0 for bucket in payload["buckets"])
    assert payload["active_keys_24h"] == 0
    assert "DATE_TRUNC('hour'" in fake_db.calls[0][0]


@pytest.mark.asyncio
async def test_live_reports_skip_dimensions_outside_the_team_view(client, test_app, monkeypatch):
    fake_db = _install(test_app, monkeypatch, TEAM)

    top = await client.get("/ui/api/spend/live/top", headers=HEADERS)
    budgets = await client.get("/ui/api/spend/live/budgets", headers=HEADERS)
    daily = await client.get("/ui/api/spend/live/daily", headers=HEADERS)
    providers = await client.get("/ui/api/spend/live/providers", headers=HEADERS)

    assert [response.status_code for response in (top, budgets, daily, providers)] == [200] * 4
    top_query, _ = fake_db.calls[0]
    assert "(s.api_key)" not in top_query
    assert "(s.team_id)" in top_query
    budget_query, budget_params = fake_db.calls[1]
    assert "deltallm_organizationtable" not in budget_query
    assert "team-1" in budget_params
    assert daily.json()["start_date"] < daily.json()["end_date"]
    for query, params in fake_db.calls[2:]:
        assert "team_id IN" in query
        assert "team-1" in params


@pytest.mark.asyncio
async def test_request_logs_bind_new_filters(client, test_app, monkeypatch):
    fake_db = _install(test_app, monkeypatch, PLATFORM)

    response = await client.get(
        "/ui/api/logs?pagination_mode=cursor&status=error&cache_hit=false"
        "&request_id=req-1&api_key=hash-1&provider=OpenAI",
        headers=HEADERS,
    )

    assert response.status_code == 200
    query, params = fake_db.calls[0]
    assert "s.status = $" in query
    assert "s.cache_hit = $" in query
    assert "s.request_id = $" in query
    assert "s.api_key = $" in query
    assert "team_alias" in query
    assert {"error", False, "req-1", "hash-1", "OpenAI"} <= set(params)


@pytest.mark.asyncio
async def test_request_logs_reject_unknown_status(client, test_app, monkeypatch):
    _install(test_app, monkeypatch, PLATFORM)

    response = await client.get("/ui/api/logs?status=maybe", headers=HEADERS)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_request_logs_since_narrows_the_start_date(client, test_app, monkeypatch):
    fake_db = _install(test_app, monkeypatch, PLATFORM)

    response = await client.get(
        "/ui/api/logs?pagination_mode=cursor&start_date=2026-10-01&since=2026-10-13T08:30:00Z",
        headers=HEADERS,
    )

    assert response.status_code == 200
    _, params = fake_db.calls[0]
    assert params[0].isoformat() == "2026-10-13T08:30:00+00:00"
