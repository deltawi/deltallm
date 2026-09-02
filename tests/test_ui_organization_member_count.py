from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from src.api.admin.endpoints.common import AuthScope
from src.auth.roles import Permission


class _OrganizationListDB:
    def __init__(self) -> None:
        now = datetime.now(tz=UTC)
        self.organizations = {
            "org-visible": {
                "organization_id": "org-visible",
                "organization_name": "Visible",
                "lifecycle_state": "active",
                "created_at": now,
                "updated_at": now,
            },
            "org-hidden": {
                "organization_id": "org-hidden",
                "organization_name": "Hidden",
                "lifecycle_state": "active",
                "created_at": now,
                "updated_at": now,
            },
        }
        self.organization_members = {
            "org-visible": {"account-1", "account-2"},
            "org-hidden": set(),
        }
        self.organization_teams = {
            "org-visible": {"team-1"},
            "org-hidden": set(),
        }
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    def _visible_organization_ids(
        self, normalized_query: str, params: tuple[Any, ...]
    ) -> list[str]:
        if "o.organization_id in ($1)" in normalized_query:
            return [str(params[0])]
        return list(self.organizations)

    async def query_raw(self, query: str, *params: Any) -> list[dict[str, Any]]:
        self.queries.append((query, params))
        normalized = " ".join(query.lower().split())
        if "from deltallm_organizationtierassignment a" in normalized:
            return []

        organization_ids = self._visible_organization_ids(normalized, params)
        if "count(*) as total" in normalized:
            return [{"total": len(organization_ids)}]
        if "from deltallm_organizationtable o" not in normalized:
            return []

        assert "from deltallm_organizationmembership om" in normalized
        assert "om.organization_id = o.organization_id" in normalized
        return [
            {
                **self.organizations[organization_id],
                "team_count": len(self.organization_teams[organization_id]),
                "member_count": len(self.organization_members[organization_id]),
            }
            for organization_id in organization_ids
        ]


def _install_db(test_app: Any, db: _OrganizationListDB) -> None:
    test_app.state.prisma_manager = type("Prisma", (), {"client": db})()
    setattr(test_app.state.settings, "master_key", "mk-test")


@pytest.mark.asyncio
async def test_list_organizations_returns_authoritative_member_counts(client, test_app) -> None:
    db = _OrganizationListDB()
    _install_db(test_app, db)

    response = await client.get(
        "/ui/api/organizations",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    organizations = {item["organization_id"]: item for item in response.json()["data"]}
    assert organizations["org-visible"]["member_count"] == 2
    assert organizations["org-hidden"]["member_count"] == 0
    assert organizations["org-visible"]["team_count"] == 1
    assert organizations["org-hidden"]["team_count"] == 0

    list_queries = [
        query
        for query, _params in db.queries
        if "FROM deltallm_organizationtable o" in query and "ORDER BY" in query
    ]
    assert len(list_queries) == 1
    assert sum("FROM deltallm_organizationmembership" in query for query, _ in db.queries) == 1


@pytest.mark.asyncio
async def test_list_organization_member_counts_remain_tenant_scoped(
    client,
    test_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _OrganizationListDB()
    _install_db(test_app, db)
    monkeypatch.setattr(
        "src.api.admin.endpoints.organizations.get_auth_scope",
        lambda request, authorization=None, x_master_key=None, required_permission=None: AuthScope(
            is_platform_admin=False,
            org_ids=["org-visible"],
            org_permissions_by_id={"org-visible": {Permission.ORG_READ}},
        ),
    )

    response = await client.get("/ui/api/organizations")

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total"] == 1
    assert [(item["organization_id"], item["member_count"]) for item in payload["data"]] == [
        ("org-visible", 2)
    ]
    list_query = next(
        query
        for query, _params in db.queries
        if "FROM deltallm_organizationtable o" in query and "ORDER BY" in query
    )
    assert "o.organization_id IN ($1)" in list_query
