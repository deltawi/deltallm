"""Admin API for MCP bundles: CRUD, validation, scope rules and the key preview."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.audit.actions import AuditAction
from src.auth.roles import OrganizationRole, TeamRole
from tests.mcp.bundle_fakes import (
    DOCS,
    GITHUB,
    JIRA,
    FakeBundleRepository,
    bundle_gateway,
    binding,
    policy,
    server,
)
from tests.test_ui_mcp import (
    _FakeMCPRegistryService,
    _FakeMCPRepository,
    _make_context,
    _RecordingAuditService,
    _set_auth_context,
)

pytestmark = pytest.mark.app

MASTER = {"Authorization": "Bearer mk-test"}
ORG_SERVER = server(
    "srv-acme", "acme-wiki", ["read"], owner_scope_type="organization", owner_scope_id="org-other"
)
MAIN_SERVER = server(
    "srv-main",
    "main-wiki",
    ["read", "write"],
    owner_scope_type="organization",
    owner_scope_id="org-main",
)


class _FakeScopeDb:
    """Answers the scope, visibility and key lookups of the bundle endpoints."""

    def __init__(self, *, visible_servers: set[str] | None = None) -> None:
        self.organizations = {"org-main", "org-other", "org-default"}
        self.teams = {"team-ops": "org-main", "team-x": "org-other", "team-default": "org-default"}
        self.visible_servers = visible_servers or set()
        self.keys = {
            "key-default": {
                "token": "key-default",
                "user_id": None,
                "team_id": "team-default",
                "organization_id": "org-default",
            },
            "key-other": {
                "token": "key-other",
                "user_id": None,
                "team_id": "team-x",
                "organization_id": "org-other",
            },
        }

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        normalized = " ".join(str(query).split())
        if "FROM deltallm_organizationtable WHERE organization_id = $1" in normalized:
            return [{"organization_id": params[0]}] if params[0] in self.organizations else []
        if "FROM deltallm_teamtable WHERE team_id = $1" in normalized:
            org = self.teams.get(params[0])
            return [{"team_id": params[0], "organization_id": org}] if org else []
        if "SELECT EXISTS(" in normalized and "FROM deltallm_mcpserver s" in normalized:
            return [{"visible": params[0] in self.visible_servers}]
        if "COUNT(*)::int AS total FROM deltallm_mcpbinding b" in normalized:
            return [{"total": 0}]
        if "FROM deltallm_mcpbinding b" in normalized:
            return []
        if "COUNT(*)::int AS total FROM deltallm_mcptoolpolicy p" in normalized:
            return [{"total": 0}]
        if "FROM deltallm_mcptoolpolicy p" in normalized:
            return []
        if "SELECT owner_account_id FROM deltallm_verificationtoken" in normalized:
            return [{"owner_account_id": None}] if params[0] in self.keys else []
        if "FROM deltallm_verificationtoken vt" in normalized:
            row = self.keys.get(params[0])
            return [row] if row else []
        raise AssertionError(f"Unexpected query: {normalized} params={params}")


async def _setup(test_app, *, servers=None, db: _FakeScopeDb | None = None):  # noqa: ANN001, ANN202
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeMCPRepository()
    for item in servers or [GITHUB, JIRA, DOCS]:
        repository.servers[item.mcp_server_id] = item
    bundles = FakeBundleRepository(repository.servers)
    bundles.team_orgs = {"team-ops": "org-main", "team-x": "org-other"}
    test_app.state.mcp_repository = repository
    test_app.state.mcp_registry_service = _FakeMCPRegistryService(repository)
    test_app.state.mcp_bundle_repository = bundles
    test_app.state.prisma_manager = type("PrismaManager", (), {"client": db or _FakeScopeDb()})()
    audit = _RecordingAuditService()
    test_app.state.audit_service = audit
    return repository, bundles, audit


ENG = {
    "bundle_key": "eng-assist",
    "name": "Engineering assistant",
    "description": "Tools for code review.",
    "members": [
        {
            "mcp_server_id": "srv-github",
            "tool_names": ["create_issue", "list_pull_requests"],
            "name_prefix": "gh",
        },
        {"mcp_server_id": "srv-jira", "all_tools": True, "name_prefix": "jira"},
    ],
}


async def test_platform_admin_crud_and_audit(client, test_app) -> None:
    _, bundles, audit = await _setup(test_app)

    created = await client.post("/ui/api/mcp-bundles", headers=MASTER, json=ENG)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["bundle_key"] == "eng-assist"
    assert body["owner_scope_type"] == "global"
    assert body["tool_count"] == 4
    assert [member["tool_count"] for member in body["members"]] == [2, 2]
    assert body["capabilities"] == {"can_mutate": True}

    listed = await client.get("/ui/api/mcp-bundles", headers=MASTER, params={"search": "eng"})
    assert [item["bundle_key"] for item in listed.json()["data"]] == ["eng-assist"]
    assert listed.json()["pagination"]["total"] == 1

    bundle_id = body["mcp_bundle_id"]
    detail = await client.get(f"/ui/api/mcp-bundles/{bundle_id}", headers=MASTER)
    assert detail.json()["members"][0]["tool_names"] == ["create_issue", "list_pull_requests"]

    updated = await client.patch(
        f"/ui/api/mcp-bundles/{bundle_id}",
        headers=MASTER,
        json={"name": "Eng", "members": [{"mcp_server_id": "srv-docs", "all_tools": True}]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Eng"
    assert updated.json()["tool_count"] == 1

    for change in (
        {"bundle_key": "other"},
        {"owner_scope_type": "organization", "owner_scope_id": "org-main"},
    ):
        refused = await client.patch(
            f"/ui/api/mcp-bundles/{bundle_id}", headers=MASTER, json=change
        )
        assert refused.status_code == 400

    deleted = await client.delete(f"/ui/api/mcp-bundles/{bundle_id}", headers=MASTER)
    assert deleted.json() == {"deleted": True, "mcp_bundle_id": bundle_id}
    assert bundles.bundles == {}
    assert (await client.get(f"/ui/api/mcp-bundles/{bundle_id}", headers=MASTER)).status_code == 404

    actions = [call[0].action for call in audit.sync_calls]
    for action in (
        AuditAction.ADMIN_MCP_BUNDLE_CREATE,
        AuditAction.ADMIN_MCP_BUNDLE_UPDATE,
        AuditAction.ADMIN_MCP_BUNDLE_DELETE,
    ):
        assert action in actions
    assert audit.sync_calls[0][0].resource_type == "mcp_bundle"


async def test_create_rejects_duplicate_names_and_names_the_conflict(client, test_app) -> None:
    await _setup(test_app)
    payload = {
        **ENG,
        "members": [
            {"mcp_server_id": "srv-github", "tool_names": ["create_issue"]},
            {"mcp_server_id": "srv-jira", "tool_names": ["create_issue"]},
        ],
    }
    response = await client.post("/ui/api/mcp-bundles", headers=MASTER, json=payload)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "'create_issue'" in detail
    assert "github.create_issue" in detail and "jira.create_issue" in detail


async def test_create_rejects_key_collisions_and_bad_members(client, test_app) -> None:
    _, bundles, _ = await _setup(test_app)
    collision = await client.post(
        "/ui/api/mcp-bundles", headers=MASTER, json={**ENG, "bundle_key": "github"}
    )
    assert collision.status_code == 409
    assert "MCP server" in collision.json()["detail"]

    bundles.add("taken", [("srv-docs", None, None)])
    taken = await client.post(
        "/ui/api/mcp-bundles", headers=MASTER, json={**ENG, "bundle_key": "taken"}
    )
    assert taken.status_code == 409

    for members, message in (
        ([{"mcp_server_id": "srv-missing", "all_tools": True}], "not found"),
        ([{"mcp_server_id": "srv-github", "tool_names": ["nope"]}], "no tool named 'nope'"),
        ([{"mcp_server_id": "srv-github", "tool_names": []}], "at least one tool"),
    ):
        response = await client.post(
            "/ui/api/mcp-bundles",
            headers=MASTER,
            json={**ENG, "bundle_key": "new", "members": members},
        )
        assert response.status_code == 400 and message in response.json()["detail"], response.text
    bad_key = await client.post(
        "/ui/api/mcp-bundles", headers=MASTER, json={**ENG, "bundle_key": "a/b"}
    )
    assert bad_key.status_code == 400


async def test_server_create_rejects_a_bundle_key(client, test_app) -> None:
    _, bundles, _ = await _setup(test_app)
    bundles.add("eng", [("srv-docs", None, None)])
    response = await client.post(
        "/ui/api/mcp-servers",
        headers=MASTER,
        json={"server_key": "eng", "name": "Eng", "base_url": "https://mcp.example.com"},
    )
    assert response.status_code == 409
    assert "bundle" in response.json()["detail"]


async def test_bundle_cannot_mix_tenants(client, test_app) -> None:
    await _setup(test_app, servers=[GITHUB, ORG_SERVER, MAIN_SERVER])
    # A global bundle cannot show an organization's server to other tenants.
    global_bundle = await client.post(
        "/ui/api/mcp-bundles",
        headers=MASTER,
        json={**ENG, "members": [{"mcp_server_id": "srv-acme", "all_tools": True}]},
    )
    assert global_bundle.status_code == 400
    assert "another organization" in global_bundle.json()["detail"]
    team_bundle = await client.post(
        "/ui/api/mcp-bundles",
        headers=MASTER,
        json={
            **ENG,
            "owner_scope_type": "team",
            "owner_scope_id": "team-ops",
            "members": [{"mcp_server_id": "srv-acme", "all_tools": True}],
        },
    )
    assert team_bundle.status_code == 400
    same_org = await client.post(
        "/ui/api/mcp-bundles",
        headers=MASTER,
        json={
            **ENG,
            "owner_scope_type": "team",
            "owner_scope_id": "team-ops",
            "members": [{"mcp_server_id": "srv-main", "all_tools": True}],
        },
    )
    assert same_org.status_code == 200, same_org.text
    assert same_org.json()["owner_scope_id"] == "team-ops"


async def test_org_admin_scope_rules(client, test_app, monkeypatch) -> None:
    db = _FakeScopeDb(visible_servers={"srv-github", "srv-main"})
    _, bundles, _ = await _setup(test_app, servers=[GITHUB, ORG_SERVER, MAIN_SERVER], db=db)
    bundles.add("main-only", [("srv-main", None, None)], owner=("organization", "org-main"))
    bundles.add("ops", [("srv-main", None, None)], owner=("team", "team-ops"))
    bundles.add("other", [("srv-acme", None, None)], owner=("organization", "org-other"))
    bundles.add("platform", [("srv-github", None, None)])
    _set_auth_context(monkeypatch, _make_context(org_role=OrganizationRole.ADMIN))

    listed = await client.get("/ui/api/mcp-bundles")
    assert [item["bundle_key"] for item in listed.json()["data"]] == ["main-only", "ops"]
    assert all(item["capabilities"]["can_mutate"] for item in listed.json()["data"])
    assert (await client.get("/ui/api/mcp-bundles/bundle-other")).status_code == 404
    assert (await client.get("/ui/api/mcp-bundles/bundle-platform")).status_code == 404
    assert (await client.delete("/ui/api/mcp-bundles/bundle-other")).status_code == 404

    created = await client.post(
        "/ui/api/mcp-bundles",
        json={**ENG, "members": [{"mcp_server_id": "srv-main", "all_tools": True}]},
    )
    assert created.status_code == 200, created.text
    assert created.json()["owner_scope_type"] == "organization"
    assert created.json()["owner_scope_id"] == "org-main"

    for payload in (
        {**ENG, "bundle_key": "g", "owner_scope_type": "global"},
        {
            **ENG,
            "bundle_key": "o",
            "owner_scope_type": "organization",
            "owner_scope_id": "org-other",
        },
        {**ENG, "bundle_key": "t", "owner_scope_type": "team", "owner_scope_id": "team-x"},
    ):
        assert (await client.post("/ui/api/mcp-bundles", json=payload)).status_code == 403
    # A server that the admin cannot see is the same as a missing server.
    hidden = await client.post(
        "/ui/api/mcp-bundles",
        json={
            **ENG,
            "bundle_key": "h",
            "members": [{"mcp_server_id": "srv-acme", "all_tools": True}],
        },
    )
    assert hidden.status_code == 400
    assert hidden.json()["detail"] == "MCP server 'srv-acme' not found"


async def test_team_member_reads_team_bundle_but_cannot_change_it(
    client, test_app, monkeypatch
) -> None:
    _, bundles, _ = await _setup(test_app, servers=[MAIN_SERVER])
    bundles.add("ops", [("srv-main", None, None)], owner=("team", "team-ops"))
    _set_auth_context(monkeypatch, _make_context(team_role=TeamRole.ADMIN))

    listed = await client.get("/ui/api/mcp-bundles")
    assert [item["bundle_key"] for item in listed.json()["data"]] == ["ops"]
    assert listed.json()["data"][0]["capabilities"]["can_mutate"] is False
    patch = await client.patch("/ui/api/mcp-bundles/bundle-ops", json={"name": "x"})
    assert patch.status_code == 403


async def test_preview_draft_lists_tools_flags_and_conflicts(client, test_app) -> None:
    repository, _, _ = await _setup(test_app)
    for record in (
        policy("srv-github", "create_issue", ("organization", "org-main"), approval="manual"),
        policy("srv-github", "delete_branch", ("organization", "org-main"), enabled=False),
        policy("srv-github", "list_pull_requests", ("organization", "org-other"), enabled=False),
    ):
        repository.policies[record.mcp_tool_policy_id] = record

    response = await client.post(
        "/ui/api/mcp-bundles/preview",
        headers=MASTER,
        json={
            "owner_scope_type": "organization",
            "owner_scope_id": "org-main",
            "members": [
                {"mcp_server_id": "srv-github", "all_tools": True},
                {"mcp_server_id": "srv-jira", "tool_names": ["search", "create_issue"]},
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    github_tools = {tool["name"]: tool for tool in body["servers"][0]["tools"]}
    assert github_tools["create_issue"]["needs_approval"] is True
    assert github_tools["delete_branch"]["disabled_by_policy"] is True
    # A policy of another organization does not mark the tool.
    assert github_tools["list_pull_requests"]["disabled_by_policy"] is False
    assert [tool["exposed_name"] for tool in body["tools"]][:2] == [
        "create_issue",
        "list_pull_requests",
    ]
    assert len(body["errors"]) == 1 and "'create_issue'" in body["errors"][0]
    assert body["key"] is None


async def test_preview_with_a_key_explains_access(client, test_app) -> None:
    servers = [GITHUB, JIRA, DOCS]
    _, bundles, _ = await _setup(test_app, servers=servers)
    org = ("organization", "org-default")
    gateway, _ = await bundle_gateway(
        servers,
        [binding("srv-github", org, ["create_issue", "list_pull_requests", "delete_branch"])],
        [
            policy("srv-github", "delete_branch", org, enabled=False),
            policy("srv-github", "create_issue", org, approval="manual"),
        ],
        bundles,
    )
    test_app.state.mcp_gateway_service = gateway
    draft = {"members": [{"mcp_server_id": "srv-github", "all_tools": True, "name_prefix": "gh"}]}

    response = await client.post(
        "/ui/api/mcp-bundles/preview", headers=MASTER, json={**draft, "api_key": "key-default"}
    )
    assert response.status_code == 200, response.text
    key = response.json()["key"]
    assert key["sees_bundle"] is True
    assert {tool["name"]: tool["access"] for tool in key["tools"]} == {
        "gh_create_issue": "needs_approval",
        "gh_list_pull_requests": "available",
        "gh_delete_branch": "disabled_by_policy",
        "gh_merge_pull_request": "no_access",
    }
    assert key["usable_count"] == 2

    team_owned = await client.post(
        "/ui/api/mcp-bundles/preview",
        headers=MASTER,
        json={
            **draft,
            "owner_scope_type": "team",
            "owner_scope_id": "team-x",
            "api_key": "key-default",
        },
    )
    team_key = team_owned.json()["key"]
    assert team_key["sees_bundle"] is False
    assert team_key["reason"]
    assert {tool["access"] for tool in team_key["tools"]} == {"no_access"}

    missing = await client.post(
        "/ui/api/mcp-bundles/preview", headers=MASTER, json={**draft, "api_key": "key-missing"}
    )
    assert missing.status_code == 404


async def test_preview_of_a_saved_bundle_and_key_scope(client, test_app, monkeypatch) -> None:
    db = _FakeScopeDb(visible_servers={"srv-main"})
    _, bundles, _ = await _setup(test_app, servers=[replace(MAIN_SERVER)], db=db)
    bundles.add("main", [("srv-main", ["read"], None)], owner=("organization", "org-main"))
    gateway, _ = await bundle_gateway([MAIN_SERVER], [], [], bundles)
    test_app.state.mcp_gateway_service = gateway
    _set_auth_context(monkeypatch, _make_context(org_role=OrganizationRole.ADMIN))

    saved = await client.post("/ui/api/mcp-bundles/preview", json={"mcp_bundle_id": "bundle-main"})
    assert saved.status_code == 200, saved.text
    assert [tool["exposed_name"] for tool in saved.json()["tools"]] == ["read"]
    # The admin cannot read a key of another organization.
    foreign_key = await client.post(
        "/ui/api/mcp-bundles/preview", json={"mcp_bundle_id": "bundle-main", "api_key": "key-other"}
    )
    assert foreign_key.status_code == 403
