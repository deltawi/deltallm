from __future__ import annotations

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
        if "COUNT(*) AS total FROM deltallm_spendlogs" in query:
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
    assert "team_id IN (SELECT team_id FROM deltallm_teamtable WHERE organization_id IN" in query
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
    assert "team_id IN (SELECT team_id FROM deltallm_teamtable WHERE organization_id IN" in logs_query
    assert "team_id IN (SELECT team_id FROM deltallm_teamtable WHERE organization_id IN" in count_query
    assert "org-1" in logs_params
    assert "org-1" in count_params


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
