from __future__ import annotations

import pytest

from src.mcp.bundle_runtime import (
    BundleToolAccess,
    explain_bundle_access,
    list_bundle_tools,
    resolve_visible_bundle,
    runtime_bundle_catalog,
)
from src.mcp.bundles import (
    MCPBundle,
    MCPBundleMember,
    MCPBundleValidationError,
    build_bundle_catalog,
    bundle_visible_to_caller,
    normalize_bundle_key,
    parse_bundle_members,
)
from src.mcp.orchestrator import MCPChatOrchestrator
from src.models.errors import InvalidRequestError
from src.models.requests import ChatCompletionRequest
from src.models.responses import UserAPIKeyAuth
from src.services.access.runtime_scopes import annotate_auth_metadata
from tests.mcp.bundle_fakes import engineering_gateway, key_auth

KEYS = {"s1": "github", "s2": "jira"}
TOOLS = {"s1": ["create_issue", "search"], "s2": ["search", "create_issue"]}


def _member(server_id: str, tools: list[str] | None = None, prefix: str | None = None):  # noqa: ANN202
    return MCPBundleMember(
        server_id=server_id, tool_names=tuple(tools) if tools else None, name_prefix=prefix
    )


def test_prefix_maps_exposed_names_to_real_tools() -> None:
    catalog = build_bundle_catalog(
        [_member("s1", None, "gh"), _member("s2", ["search"])],
        server_keys=KEYS,
        server_tools=TOOLS,
        strict=True,
    )
    assert list(catalog) == ["gh_create_issue", "gh_search", "search"]
    assert catalog["gh_search"].namespaced_name == "github.search"
    assert catalog["search"].namespaced_name == "jira.search"


def test_duplicate_exposed_names_are_rejected_with_both_tools_named() -> None:
    with pytest.raises(MCPBundleValidationError) as caught:
        build_bundle_catalog(
            [_member("s1", ["search"]), _member("s2", ["search"])],
            server_keys=KEYS,
            server_tools=TOOLS,
            strict=True,
        )
    message = str(caught.value)
    assert "'search'" in message and "github.search" in message and "jira.search" in message


def test_prefix_collision_with_an_unprefixed_name_is_rejected() -> None:
    # The prefix makes `gh_search`, and the second server also has a tool `gh_search`.
    with pytest.raises(MCPBundleValidationError, match="gh_search"):
        build_bundle_catalog(
            [_member("s1", ["search"], "gh"), _member("s2", ["gh_search"])],
            server_keys=KEYS,
            server_tools={"s1": ["search"], "s2": ["gh_search"]},
            strict=True,
        )


def test_runtime_catalog_keeps_the_first_member_for_a_new_conflict() -> None:
    catalog = build_bundle_catalog(
        [_member("s1"), _member("s2")], server_keys=KEYS, server_tools=TOOLS, strict=False
    )
    assert catalog["search"].server_key == "github"
    assert catalog["create_issue"].server_key == "github"


def test_unknown_tool_and_reserved_name_are_rejected() -> None:
    with pytest.raises(MCPBundleValidationError, match="no tool named 'missing'"):
        build_bundle_catalog(
            [_member("s1", ["missing"])], server_keys=KEYS, server_tools=TOOLS, strict=True
        )
    with pytest.raises(MCPBundleValidationError, match="reserved"):
        build_bundle_catalog(
            [_member("s1", ["tool_search"], "mcp")],
            server_keys=KEYS,
            server_tools={"s1": ["tool_search"]},
            strict=True,
        )


def test_member_and_key_parsing() -> None:
    assert normalize_bundle_key(" Eng-Assist ") == "eng-assist"
    for bad in ("", "-x", "a/b", "a" * 64):
        with pytest.raises(MCPBundleValidationError):
            normalize_bundle_key(bad)
    members = parse_bundle_members(
        [{"mcp_server_id": "s1", "all_tools": True, "name_prefix": " gh "}]
    )
    assert members == (MCPBundleMember("s1", None, "gh"),)
    with pytest.raises(MCPBundleValidationError, match="one time"):
        parse_bundle_members(
            [
                {"mcp_server_id": "s1", "all_tools": True},
                {"mcp_server_id": "s1", "all_tools": True},
            ]
        )
    with pytest.raises(MCPBundleValidationError, match="at least one tool"):
        parse_bundle_members([{"mcp_server_id": "s1", "tool_names": []}])
    with pytest.raises(MCPBundleValidationError, match="name_prefix"):
        parse_bundle_members([{"mcp_server_id": "s1", "all_tools": True, "name_prefix": "a b"}])


@pytest.mark.parametrize(
    ("owner", "team", "org", "visible"),
    [
        (("global", None), "team-a", "org-a", True),
        (("organization", "org-a"), "team-a", "org-a", True),
        (("organization", "org-b"), "team-a", "org-a", False),
        (("team", "team-a"), "team-a", "org-a", True),
        (("team", "team-b"), "team-a", "org-a", False),
        (("team", "team-a"), None, "org-a", False),
    ],
)
def test_owner_scope_decides_bundle_visibility(owner, team, org, visible) -> None:  # noqa: ANN001
    bundle = MCPBundle("b", "eng", "Eng", owner[0], owner[1], True, ())
    assert bundle_visible_to_caller(bundle, key_auth(team_id=team, organization_id=org)) is visible
    disabled = MCPBundle("b", "eng", "Eng", owner[0], owner[1], False, ())
    assert bundle_visible_to_caller(disabled, key_auth(team_id=team, organization_id=org)) is False


def test_master_key_sees_every_enabled_bundle() -> None:
    master = annotate_auth_metadata(
        UserAPIKeyAuth(api_key="master_key"), auth_source="master_key", is_master_key=True
    )
    bundle = MCPBundle("b", "eng", "Eng", "team", "team-x", True, ())
    assert bundle_visible_to_caller(bundle, master) is True


async def test_bundle_shows_only_tools_that_the_key_can_already_use() -> None:
    gateway, _, _ = await engineering_gateway()
    auth = key_auth()
    bundle = resolve_visible_bundle(gateway, auth, "eng")
    assert bundle is not None
    tools = await list_bundle_tools(gateway, auth, bundle)
    # merge_pull_request: not in the binding. delete_branch: disabled by policy.
    assert [item.exposed_name for item in tools] == [
        "gh_create_issue",
        "gh_list_pull_requests",
        "jira_search",
        "jira_create_issue",
    ]
    assert tools[0].tool.namespaced_name == "github.create_issue"


async def test_a_key_without_bindings_sees_no_bundle_tools() -> None:
    gateway, _, _ = await engineering_gateway()
    outsider = key_auth(team_id="team-x", organization_id="org-other")
    bundle = resolve_visible_bundle(gateway, outsider, "eng")
    assert bundle is not None
    assert await list_bundle_tools(gateway, outsider, bundle) == []


async def test_bundle_of_another_tenant_is_unknown() -> None:
    gateway, _, _ = await engineering_gateway(owner=("organization", "org-other"))
    assert resolve_visible_bundle(gateway, key_auth(), "eng") is None
    assert resolve_visible_bundle(gateway, key_auth(), "missing") is None


async def test_explain_access_names_the_reason_per_tool() -> None:
    gateway, _, _ = await engineering_gateway()
    bundle = resolve_visible_bundle(gateway, key_auth(), "eng")
    assert bundle is not None
    catalog = await runtime_bundle_catalog(gateway, bundle.members)
    results = {
        item.target.exposed_name: item.access
        for item in await explain_bundle_access(gateway, key_auth(), catalog)
    }
    assert results == {
        "gh_create_issue": BundleToolAccess.AVAILABLE,
        "gh_list_pull_requests": BundleToolAccess.AVAILABLE,
        "gh_delete_branch": BundleToolAccess.DISABLED_BY_POLICY,
        "gh_merge_pull_request": BundleToolAccess.NO_ACCESS,
        "jira_search": BundleToolAccess.AVAILABLE,
        "jira_create_issue": BundleToolAccess.NEEDS_APPROVAL,
    }


def _chat(tools: list[dict]) -> ChatCompletionRequest:  # noqa: ANN001
    return ChatCompletionRequest.model_validate(
        {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}], "tools": tools}
    )


async def test_chat_payload_uses_exposed_names_and_maps_calls_back() -> None:
    gateway, _, _ = await engineering_gateway()
    orchestrator = MCPChatOrchestrator(gateway)
    payload, tool_set = await orchestrator.prepare_payload(
        key_auth(),
        _chat(
            [{"type": "mcp", "server": "eng", "allowed_tools": ["gh_create_issue", "jira_search"]}]
        ),
    )
    names = [tool.function["name"] for tool in payload.tools]
    assert names == ["gh_create_issue", "jira_search"]
    assert tool_set.resolved["gh_create_issue"].gateway_name == "github.create_issue"
    assert tool_set.resolved["jira_search"].gateway_name == "jira.search"


async def test_chat_rejects_manual_approval_tools_of_a_bundle_like_a_server() -> None:
    gateway, _, _ = await engineering_gateway()
    orchestrator = MCPChatOrchestrator(gateway)
    with pytest.raises(InvalidRequestError, match="manual approval"):
        await orchestrator.prepare_payload(key_auth(), _chat([{"type": "mcp", "server": "eng"}]))


async def test_chat_tool_search_over_a_bundle_skips_approval_tools() -> None:
    gateway, _, _ = await engineering_gateway()
    orchestrator = MCPChatOrchestrator(gateway)
    payload, tool_set = await orchestrator.prepare_payload(
        key_auth(), _chat([{"type": "mcp", "server": "eng", "tool_search": True}])
    )
    assert [tool.function["name"] for tool in payload.tools] == ["mcp_tool_search"]
    assert set(tool_set.searchable) == {"gh_create_issue", "gh_list_pull_requests", "jira_search"}
    hits = tool_set.search.search("pull requests")
    assert hits[0].name == "gh_list_pull_requests"


async def test_chat_rejects_two_tools_with_one_name() -> None:
    gateway, _, bundles = await engineering_gateway()
    bundles.add("support", [("srv-docs", ["search"], None)])
    bundles.add("jira-plain", [("srv-jira", ["search"], None)])
    await gateway.governance_service.reload()
    orchestrator = MCPChatOrchestrator(gateway)
    with pytest.raises(InvalidRequestError, match="'search'"):
        await orchestrator.prepare_payload(
            key_auth(),
            _chat([{"type": "mcp", "server": "support"}, {"type": "mcp", "server": "jira-plain"}]),
        )
    function_tool = {"type": "function", "function": {"name": "search", "parameters": {}}}
    with pytest.raises(InvalidRequestError, match="also a tool of MCP bundle"):
        await orchestrator.prepare_payload(
            key_auth(), _chat([function_tool, {"type": "mcp", "server": "support"}])
        )


async def test_unknown_or_foreign_bundle_fails_like_an_unknown_server() -> None:
    gateway, _, _ = await engineering_gateway(owner=("team", "team-other"))
    orchestrator = MCPChatOrchestrator(gateway)
    for name in ("eng", "nothing"):
        with pytest.raises(InvalidRequestError, match=f"server '{name}'"):
            await orchestrator.prepare_payload(key_auth(), _chat([{"type": "mcp", "server": name}]))


async def test_server_requests_do_not_read_bundles() -> None:
    gateway, _, _ = await engineering_gateway()
    real = gateway.governance_service

    class _NoBundleReads:
        def __getattr__(self, name: str):  # noqa: ANN204
            if name == "get_bundle":
                raise AssertionError("a server request must not look up bundles")
            return getattr(real, name)

    gateway.governance_service = _NoBundleReads()  # type: ignore[assignment]
    orchestrator = MCPChatOrchestrator(gateway)
    payload, _ = await orchestrator.prepare_payload(
        key_auth(), _chat([{"type": "mcp", "server": "docs"}])
    )
    assert [tool.function["name"] for tool in payload.tools] == ["docs.search"]
