from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from src.mcp.models import MCPToolCallResult
from src.mcp.tool_search import SEARCH_TOOL_NAME, SearchableTool, ToolSearchIndex, tokenize
from tests.mcp.test_chat_execution import _RecordingAuditService

TOOLS = [
    SearchableTool(
        "github.create_issue",
        "Create an issue in a repository.",
        {"type": "object", "properties": {"title": {"type": "string"}}},
    ),
    SearchableTool("github.list_pull_requests", "List the pull requests of a repository.", {}),
    SearchableTool("jira.createTicket", "Open a Jira ticket for a project.", {}),
    SearchableTool("docs.search", "Search the product documentation.", {}),
]


def test_tokenize_splits_names_and_camel_case() -> None:
    assert tokenize("jira.createTicket") == ["jira", "create", "ticket"]
    assert tokenize("list_pull_requests") == ["list", "pull", "requests"]


def test_search_ranks_name_matches_first_and_bounds_limit() -> None:
    index = ToolSearchIndex(TOOLS)
    hits = index.search("create a github issue")
    assert hits[0].name == "github.create_issue"
    assert [hit.name for hit in index.search("ticket", limit=50)] == ["jira.createTicket"]
    assert index.search("documentation")[0].name == "docs.search"
    assert index.search("") == []
    assert index.search("unrelated words zzz") == []
    assert len(index.search("a", limit=0)) <= 1


class _CatalogGateway:
    def __init__(self, *, manual: set[str] | None = None) -> None:
        self.manual = manual or set()
        self.tool_calls: list[str] = []

    async def list_visible_tools(self, auth):  # noqa: ANN001, ANN201
        del auth
        return [
            SimpleNamespace(
                server_key=tool.name.split(".")[0],
                original_name=tool.name.split(".", 1)[1],
                namespaced_name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema or {"type": "object"},
            )
            for tool in TOOLS
        ]

    async def tool_requires_manual_approval(self, auth, *, server_key, tool_name):  # noqa: ANN001, ANN201
        del auth
        return f"{server_key}.{tool_name}" in self.manual

    async def call_tool(self, auth, *, namespaced_tool_name, arguments, **kwargs):  # noqa: ANN001, ANN003, ANN201
        del auth, arguments, kwargs
        self.tool_calls.append(namespaced_tool_name)
        return MCPToolCallResult(content=[{"type": "text", "text": "issue 42 created"}])


def _tool_call(name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{call_id}",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _answer() -> dict[str, Any]:
    return {
        "id": "chatcmpl-done",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "done"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


@pytest.mark.app
async def test_tool_search_loads_matching_tools_for_the_next_hop(client, test_app) -> None:
    gateway = _CatalogGateway(manual={"github.list_pull_requests"})
    test_app.state.mcp_gateway_service = gateway
    test_app.state.audit_service = _RecordingAuditService()
    upstream: list[dict[str, Any]] = []

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN202
        del url, headers, timeout
        upstream.append(json)
        if len(upstream) == 1:
            return httpx.Response(
                200, json=_tool_call(SEARCH_TOOL_NAME, {"query": "create github issue"}, "s1")
            )
        if len(upstream) == 2:
            return httpx.Response(
                200, json=_tool_call("github.create_issue", {"title": "bug"}, "c1")
            )
        return httpx.Response(200, json=_answer())

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "File a bug"}],
            "tools": [{"type": "mcp", "server": "*", "tool_search": True}],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "done"
    first_tools = [tool["function"]["name"] for tool in upstream[0]["tools"]]
    assert first_tools == [SEARCH_TOOL_NAME]
    second_tools = [tool["function"]["name"] for tool in upstream[1]["tools"]]
    assert second_tools[0] == SEARCH_TOOL_NAME
    assert "github.create_issue" in second_tools
    # Tools that need manual approval are left out of search.
    assert "github.list_pull_requests" not in second_tools
    search_result = json.loads(upstream[1]["messages"][-1]["content"])
    assert search_result["tools"][0]["name"] == "github.create_issue"
    assert gateway.tool_calls == ["github.create_issue"]
    assert response.json()["usage"]["total_tokens"] == 6


@pytest.mark.app
async def test_all_servers_needs_tool_search(client, test_app) -> None:
    test_app.state.mcp_gateway_service = _CatalogGateway()
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "mcp", "server": "*"}],
        },
    )
    assert response.status_code == 400
    assert "tool_search" in response.json()["error"]["message"]


@pytest.mark.app
async def test_direct_tools_stay_and_search_covers_the_rest(client, test_app) -> None:
    test_app.state.mcp_gateway_service = _CatalogGateway()
    upstream: list[dict[str, Any]] = []

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN202
        del url, headers, timeout
        upstream.append(json)
        return httpx.Response(200, json=_answer())

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {"type": "mcp", "server": "docs"},
                {"type": "mcp", "server": "*", "tool_search": True},
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert [tool["function"]["name"] for tool in upstream[0]["tools"]] == [
        "docs.search",
        SEARCH_TOOL_NAME,
    ]


@pytest.mark.app
async def test_tool_search_lists_only_tools_within_the_load_budget(
    client, test_app, monkeypatch
) -> None:
    from src.mcp import orchestrator

    monkeypatch.setattr(orchestrator, "MAX_LOADED_TOOLS", 1)
    test_app.state.mcp_gateway_service = _CatalogGateway()
    test_app.state.audit_service = _RecordingAuditService()
    upstream: list[dict[str, Any]] = []

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN202
        del url, headers, timeout
        upstream.append(json)
        if len(upstream) == 1:
            return httpx.Response(
                200, json=_tool_call(SEARCH_TOOL_NAME, {"query": "github", "limit": 5.0}, "")
            )
        return httpx.Response(200, json=_answer())

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "mcp", "server": "*", "tool_search": True}],
        },
    )

    assert response.status_code == 200, response.text
    search_message = upstream[1]["messages"][-1]
    listed = [tool["name"] for tool in json.loads(search_message["content"])["tools"]]
    declared = [tool["function"]["name"] for tool in upstream[1]["tools"]]
    assert len(listed) == 1
    assert declared == [SEARCH_TOOL_NAME, *listed]
    assert search_message["tool_call_id"] == f"{SEARCH_TOOL_NAME}_0"


@pytest.mark.app
async def test_tool_search_rejects_a_server_whose_tools_all_need_approval(client, test_app) -> None:
    test_app.state.mcp_gateway_service = _CatalogGateway(manual={"docs.search"})
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "mcp", "server": "docs", "tool_search": True}],
        },
    )
    assert response.status_code == 400
    assert "manual approval" in response.json()["error"]["message"]


@pytest.mark.app
async def test_all_servers_allowed_tools_accepts_namespaced_names(client, test_app) -> None:
    test_app.state.mcp_gateway_service = _CatalogGateway()
    upstream: list[dict[str, Any]] = []

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN202
        del url, headers, timeout
        upstream.append(json)
        return (
            httpx.Response(200, json=_tool_call(SEARCH_TOOL_NAME, {"query": "search create"}, "s1"))
            if len(upstream) == 1
            else httpx.Response(200, json=_answer())
        )

    test_app.state.http_client.post = post
    test_app.state.audit_service = _RecordingAuditService()
    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {
                    "type": "mcp",
                    "server": "*",
                    "tool_search": True,
                    "allowed_tools": ["docs.search"],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    listed = json.loads(upstream[1]["messages"][-1]["content"])["tools"]
    assert [tool["name"] for tool in listed] == ["docs.search"]
