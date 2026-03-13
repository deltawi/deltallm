from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from src.api.admin.endpoints.common import AuthScope


class FakeSpendDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def query_raw(self, query: str, *params):
        self.calls.append((query, params))
        if "COUNT(*) AS total_requests" in query:
            return [{"total_spend": 0, "total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_requests": 0}]
        if "COUNT(*) AS total FROM" in query:
            return [{"total": 0}]
        if "GROUP BY" in query:
            return []
        return []


@pytest.mark.asyncio
async def test_spend_summary_applies_org_scope_for_non_platform(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.ui.routes.get_auth_scope",
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
    assert "org-1" in params


@pytest.mark.asyncio
async def test_spend_summary_uses_event_scope(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.ui.routes.get_auth_scope",
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
        "src.ui.routes.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=["org-1"],
            team_ids=[],
        ),
    )

    response = await client.get("/ui/api/logs", headers={"Authorization": "Bearer mk-test"})
    assert response.status_code == 200

    logs_query, logs_params = fake_db.calls[0]
    count_query, count_params = fake_db.calls[1]
    assert "FROM deltallm_spendlog_events" in logs_query
    assert "organization_id IN" in logs_query
    assert "organization_id IN" in count_query
    assert "org-1" in logs_params
    assert "org-1" in count_params


@pytest.mark.asyncio
async def test_spend_logs_use_normalized_event_columns(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.ui.routes.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=True,
            org_ids=[],
            team_ids=[],
        ),
    )

    response = await client.get("/ui/api/logs", headers={"Authorization": "Bearer mk-test"})
    assert response.status_code == 200

    logs_query, count_query = fake_db.calls[0], fake_db.calls[1]
    assert "FROM deltallm_spendlog_events" in logs_query[0]
    assert "input_tokens AS prompt_tokens" in logs_query[0]
    assert 'user_id AS "user"' in logs_query[0]
    assert "FROM deltallm_spendlog_events" in count_query[0]


@pytest.mark.asyncio
async def test_spend_report_not_scoped_for_platform_admin(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.ui.routes.get_auth_scope",
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
async def test_grouped_spend_report_applies_org_scope_for_non_platform(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.ui.routes.get_auth_scope",
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
    assert "LEFT JOIN deltallm_teamtable t ON t.team_id = s.team_id" in query
    assert "COUNT(*) OVER()" in query
    assert "org-1" in params


@pytest.mark.asyncio
async def test_grouped_spend_report_supports_api_key_search(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.ui.routes.get_auth_scope",
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
        "src.ui.routes.get_auth_scope",
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
    assert "GROUP BY s.model" in query
    assert "GROUP BY s.model, NULL" not in query


@pytest.mark.asyncio
async def test_spend_endpoints_cast_date_filters_to_timestamp(client, test_app, monkeypatch):
    fake_db = FakeSpendDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    setattr(test_app.state.settings, "master_key", "mk-test")

    monkeypatch.setattr(
        "src.ui.routes.get_auth_scope",
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
