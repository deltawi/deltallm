"""MCP bundles on `/mcp/<bundle>` and on chat/responses requests."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from src.mcp.tool_search import SEARCH_TOOL_NAME
from tests.mcp.bundle_fakes import engineering_gateway
from tests.mcp.test_chat_execution import _RecordingAuditService
from tests.mcp.test_chat_streaming import _answer_hop, _data, _events, _script, _tool_call_hop
from tests.mcp.test_tool_search import _answer, _tool_call

pytestmark = pytest.mark.app


def _headers(test_app) -> dict[str, str]:  # noqa: ANN001
    return {"Authorization": f"Bearer {test_app.state._test_key}"}


async def _install(test_app, **kwargs: Any):  # noqa: ANN001, ANN202
    gateway, transport, bundles = await engineering_gateway(**kwargs)
    test_app.state.mcp_gateway_service = gateway
    test_app.state.audit_service = _RecordingAuditService()
    return gateway, transport, bundles


async def _rpc(client, test_app, path: str, method: str, params: dict | None = None):  # noqa: ANN001, ANN202
    return await client.post(
        path,
        headers=_headers(test_app),
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


async def test_bundle_endpoint_lists_only_bundle_tools_the_key_can_use(client, test_app) -> None:
    # The test key allows two requests per minute, so each test sends at most two.
    await _install(test_app)

    listed = await _rpc(client, test_app, "/mcp/eng", "tools/list")
    names = [tool["name"] for tool in listed.json()["result"]["tools"]]
    assert names == ["gh_create_issue", "gh_list_pull_requests", "jira_search", "jira_create_issue"]

    # The plain endpoint is unchanged: namespaced names of all visible servers.
    plain = await _rpc(client, test_app, "/mcp", "tools/list")
    plain_names = [tool["name"] for tool in plain.json()["result"]["tools"]]
    assert "docs.search" in plain_names and "github.create_issue" in plain_names


async def test_bundle_endpoint_answers_initialize_and_ping(client, test_app) -> None:
    await _install(test_app)
    initialize = await _rpc(client, test_app, "/mcp/eng", "initialize")
    assert initialize.json()["result"]["capabilities"] == {"tools": {}}
    assert (await _rpc(client, test_app, "/mcp/eng", "ping")).json()["result"] == {}


async def test_bundle_tool_call_runs_the_real_tool(client, test_app) -> None:
    _, transport, _ = await _install(test_app)

    response = await _rpc(
        client,
        test_app,
        "/mcp/eng",
        "tools/call",
        {"name": "gh_create_issue", "arguments": {"title": "bug"}},
    )
    body = response.json()
    assert "error" not in body, body
    assert transport.calls[-1][0:2] == ("github", "create_issue")
    assert transport.calls[-1][3] == {"title": "bug"}
    attempt = test_app.state.audit_service.records[0]
    assert attempt.resource_id == "github.create_issue"
    assert attempt.metadata["bundle_key"] == "eng"


@pytest.mark.parametrize(
    "name", ["gh_merge_pull_request", "gh_delete_branch", "github.create_issue", "nothing"]
)
async def test_bundle_tool_call_cannot_reach_a_hidden_tool(client, test_app, name: str) -> None:
    _, transport, _ = await _install(test_app)

    response = await _rpc(client, test_app, "/mcp/eng", "tools/call", {"name": name})
    error = response.json()["error"]
    assert error["code"] == -32004
    assert error["message"] == f"Unknown MCP tool '{name}'"
    assert transport.calls == []


async def test_bundle_tool_that_needs_approval_still_needs_approval(client, test_app) -> None:
    _, transport, _ = await _install(test_app)

    response = await _rpc(client, test_app, "/mcp/eng", "tools/call", {"name": "jira_create_issue"})
    # The policy needs a manual approval, and this gateway has no approval service.
    assert response.json()["error"]["code"] == -32003
    assert "approval" in response.json()["error"]["message"]
    assert transport.calls == []


@pytest.mark.parametrize("owner", [("organization", "org-other"), ("team", "team-other")])
async def test_bundle_of_another_scope_is_not_found(client, test_app, owner) -> None:  # noqa: ANN001
    await _install(test_app, owner=owner)

    for path in ("/mcp/eng", "/mcp/missing"):
        response = await _rpc(client, test_app, path, "tools/list")
        assert response.status_code == 404
        assert response.json() == {"detail": "MCP bundle not found"}


async def test_team_bundle_is_visible_to_its_team(client, test_app) -> None:
    await _install(test_app, owner=("team", "team-default"))
    response = await _rpc(client, test_app, "/mcp/eng", "tools/list")
    assert response.status_code == 200
    assert len(response.json()["result"]["tools"]) == 4


async def test_chat_with_a_bundle_runs_the_mapped_tool(client, test_app) -> None:
    _, transport, _ = await _install(test_app)
    upstream: list[dict[str, Any]] = []

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN202
        del url, headers, timeout
        upstream.append(json)
        if len(upstream) == 1:
            return httpx.Response(200, json=_tool_call("gh_create_issue", {"title": "x"}, "c1"))
        return httpx.Response(200, json=_answer())

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(test_app),
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "File a bug"}],
            "tools": [
                {
                    "type": "mcp",
                    "server": "eng",
                    "allowed_tools": ["gh_create_issue", "gh_list_pull_requests"],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "done"
    assert [tool["function"]["name"] for tool in upstream[0]["tools"]] == [
        "gh_create_issue",
        "gh_list_pull_requests",
    ]
    assert transport.calls == [("github", "create_issue", transport.calls[0][2], {"title": "x"})]
    tool_message = upstream[1]["messages"][-1]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "c1"


async def test_chat_tool_search_on_a_bundle(client, test_app) -> None:
    _, transport, _ = await _install(test_app)
    upstream: list[dict[str, Any]] = []

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN202
        del url, headers, timeout
        upstream.append(json)
        if len(upstream) == 1:
            return httpx.Response(
                200, json=_tool_call(SEARCH_TOOL_NAME, {"query": "list pull requests"}, "s1")
            )
        if len(upstream) == 2:
            return httpx.Response(200, json=_tool_call("gh_list_pull_requests", {}, "c1"))
        return httpx.Response(200, json=_answer())

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(test_app),
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Open PRs?"}],
            "tools": [{"type": "mcp", "server": "eng", "tool_search": True}],
        },
    )
    assert response.status_code == 200, response.text
    assert [tool["function"]["name"] for tool in upstream[0]["tools"]] == [SEARCH_TOOL_NAME]
    search_result = json.loads(upstream[1]["messages"][-1]["content"])
    assert search_result["tools"][0]["name"] == "gh_list_pull_requests"
    assert "gh_list_pull_requests" in [tool["function"]["name"] for tool in upstream[1]["tools"]]
    assert [call[0:2] for call in transport.calls] == [("github", "list_pull_requests")]


async def test_chat_stream_with_a_bundle_runs_the_mapped_tool(client, test_app) -> None:
    _, transport, _ = await _install(test_app)
    upstream = _script(test_app, [_tool_call_hop("jira_search"), _answer_hop()])

    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(test_app),
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "What is DeltaLLM?"}],
            "stream": True,
            "tools": [{"type": "mcp", "server": "eng", "allowed_tools": ["jira_search"]}],
        },
    )
    assert response.status_code == 200, response.text
    events = _events(response.text)
    assert ": mcp_tool_call jira_search" in events
    text = "".join(
        choice["delta"].get("content") or ""
        for chunk in _data(events)
        for choice in chunk["choices"]
    )
    assert text == "Let me search. It is a gateway."
    assert [tool["function"]["name"] for tool in upstream[0]["tools"]] == ["jira_search"]
    assert [call[0:2] for call in transport.calls] == [("jira", "search")]
    assert transport.calls[0][3] == {"query": "delta"}


async def test_responses_with_a_bundle(client, test_app) -> None:
    _, transport, _ = await _install(test_app)
    _script(test_app, [_tool_call_hop("jira_search"), _answer_hop()])

    response = await client.post(
        "/v1/responses",
        headers=_headers(test_app),
        json={
            "model": "gpt-4o-mini",
            "input": "What is DeltaLLM?",
            "stream": True,
            "tools": [{"type": "mcp", "server": "eng", "allowed_tools": ["jira_search"]}],
        },
    )
    assert response.status_code == 200, response.text
    assert [call[0:2] for call in transport.calls] == [("jira", "search")]


async def test_chat_with_a_foreign_bundle_fails_like_an_unknown_server(client, test_app) -> None:
    await _install(test_app, owner=("organization", "org-other"))
    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(test_app),
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "mcp", "server": "eng"}],
        },
    )
    assert response.status_code == 400
    assert "No visible MCP tools are available for server 'eng'" in response.text
