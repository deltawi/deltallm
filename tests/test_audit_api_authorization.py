from __future__ import annotations

from typing import Any

import pytest

from src.auth.roles import OrganizationRole, PlatformRole, TeamRole
from src.models.platform_auth import PlatformAuthContext


class FakeAuditDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def query_raw(self, query: str, *params):
        self.calls.append((query, params))
        normalized = " ".join(query.lower().split())
        if "count(*) as total from deltallm_auditevent" in normalized:
            return [{"total": 1}]
        if "from deltallm_auditevent" in normalized:
            return [{"event_id": "evt-1", "organization_id": "org-1", "action": "CHAT_COMPLETIONS_CREATE", "metadata": {}}]
        return []


def _set_auth_context(monkeypatch: pytest.MonkeyPatch, context: PlatformAuthContext | None) -> None:
    monkeypatch.setattr("src.middleware.platform_auth.get_platform_auth_context", lambda request: context)
    monkeypatch.setattr("src.middleware.admin.get_platform_auth_context", lambda request: context)


def _make_context(*, platform_role: str = "platform_user", org_role: str | None = None, team_role: str | None = None) -> PlatformAuthContext:
    org_memberships = [{"organization_id": "org-1", "role": org_role}] if org_role else []
    team_memberships = [{"team_id": "team-1", "role": team_role}] if team_role else []
    return PlatformAuthContext(
        account_id="acc-1",
        email="user@example.com",
        role=platform_role,
        organization_memberships=org_memberships,
        team_memberships=team_memberships,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("context", "expected_status"),
    [
        (_make_context(platform_role=PlatformRole.ADMIN), 200),
        (_make_context(org_role=OrganizationRole.ADMIN), 200),
        (_make_context(org_role=OrganizationRole.OWNER), 200),
        (_make_context(org_role=OrganizationRole.BILLING), 403),
        (_make_context(org_role=OrganizationRole.AUDITOR), 403),
        (_make_context(org_role=OrganizationRole.MEMBER), 403),
        (_make_context(team_role=TeamRole.ADMIN), 403),
        (_make_context(team_role=TeamRole.DEVELOPER), 403),
        (_make_context(team_role=TeamRole.VIEWER), 403),
    ],
)
async def test_audit_events_role_matrix(client, test_app, monkeypatch, context: PlatformAuthContext, expected_status: int):
    fake_db = FakeAuditDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    _set_auth_context(monkeypatch, context)

    response = await client.get("/ui/api/audit/events")
    assert response.status_code == expected_status


@pytest.mark.asyncio
async def test_audit_events_scope_filter_for_org_admin_not_platform_admin(client, test_app, monkeypatch):
    fake_db = FakeAuditDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()

    _set_auth_context(monkeypatch, _make_context(org_role=OrganizationRole.ADMIN))
    org_response = await client.get("/ui/api/audit/events")
    assert org_response.status_code == 200
    org_query, org_params = fake_db.calls[0]
    assert "organization_id IN (" in org_query
    assert "org-1" in org_params

    fake_db.calls.clear()
    _set_auth_context(monkeypatch, _make_context(platform_role=PlatformRole.ADMIN))
    platform_response = await client.get("/ui/api/audit/events")
    assert platform_response.status_code == 200
    platform_query, _ = fake_db.calls[0]
    assert "organization_id IN (" not in platform_query
