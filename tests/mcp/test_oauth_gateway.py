"""Gateway tool calls on ``oauth2`` MCP servers. Fake HTTP only."""

from __future__ import annotations

import json
from dataclasses import replace

from src.db.mcp.mcp import MCPServerBindingRecord, MCPServerRecord
from src.mcp.exceptions import MCPAuthError
from src.mcp.gateway import MCPGatewayService
from src.mcp.models import MCPToolCallResult, MCPToolSchema
from src.mcp.orchestrator import MCPChatOrchestrator
from src.models.responses import UserAPIKeyAuth
from tests.mcp.oauth_fakes import ACCOUNT, BASE_URL, OAuthHarness
from tests.test_mcp_gateway import _AuditSink, _FakeRegistry, _server

_TOOL = MCPToolSchema(
    name="list_issues", description="List issues", input_schema={"type": "object"}
)


class _RecordingTransport:
    def __init__(self, *, reject_tokens: set[str] | None = None) -> None:
        self.reject_tokens = set(reject_tokens or ())
        self.tokens: list[str | None] = []

    async def call_tool(self, server, *, tool_name, arguments=None, request_headers=None):  # noqa: ANN001, ANN201
        self.tokens.append(server.oauth_access_token)
        if server.oauth_access_token in self.reject_tokens:
            raise MCPAuthError("MCP server rejected the credentials")
        return MCPToolCallResult(
            content=[{"type": "text", "text": f"{server.server_key}:{tool_name}"}], is_error=False
        )


def _auth(owner_account_id: str | None = ACCOUNT) -> UserAPIKeyAuth:
    return UserAPIKeyAuth(
        api_key="sk-user-1",
        team_id="team-ops",
        organization_id="org-acme",
        owner_account_id=owner_account_id,
    )


async def _gateway(
    harness: OAuthHarness, transport: _RecordingTransport, *, oauth: bool = True
) -> tuple[MCPGatewayService, MCPServerRecord]:
    plain = _server("srv-1", "github", capabilities=[_TOOL])
    if oauth:
        configured = await harness.configured_server({"client_id": "cid", "scopes": "repo"})
        server = replace(
            plain,
            name="GitHub",
            base_url=configured.base_url,
            auth_mode="oauth2",
            auth_config=configured.auth_config,
        )
        harness.servers[server.mcp_server_id] = server
    else:
        server = plain
    registry = _FakeRegistry(
        servers=[server],
        bindings=[MCPServerBindingRecord("bind-1", "srv-1", "team", "team-ops", True, None)],
        policies=[],
    )
    gateway = MCPGatewayService(
        registry,  # type: ignore[arg-type]
        transport,  # type: ignore[arg-type]
        oauth_service=harness.service,
    )
    return gateway, server


async def test_tool_call_without_connection_returns_connection_required() -> None:
    harness = OAuthHarness()
    transport = _RecordingTransport()
    gateway, _ = await _gateway(harness, transport)

    tools = await gateway.list_visible_tools(_auth())
    assert [tool.namespaced_name for tool in tools] == ["github.list_issues"]

    result = await gateway.call_tool(
        _auth(), namespaced_tool_name="github.list_issues", arguments={}
    )

    assert result.is_error is True
    assert transport.tokens == []
    error = result.structured_content["error"]
    assert error == {
        "type": "mcp_connection_required",
        "server": "github",
        "message": "Connect your GitHub account to use this tool.",
        "connect_url": f"{BASE_URL}/ui/connect/github",
    }
    assert result.content[0]["text"] == (
        f"Connect your GitHub account to use this tool.\nOpen {BASE_URL}/ui/connect/github"
    )
    assert result.metadata["server_key"] == "github"
    assert result.metadata["error_type"] == "mcp_connection_required"


async def test_connect_url_is_left_out_without_public_base_url() -> None:
    harness = OAuthHarness()
    gateway, _ = await _gateway(harness, _RecordingTransport())
    harness.base_url = None

    result = await gateway.call_tool(
        _auth(), namespaced_tool_name="github.list_issues", arguments={}
    )

    assert "connect_url" not in result.structured_content["error"]
    assert result.content[0]["text"] == "Connect your GitHub account to use this tool."


async def test_key_without_owner_gets_user_key_required() -> None:
    harness = OAuthHarness()
    gateway, _ = await _gateway(harness, _RecordingTransport())
    harness.repository.calls.clear()

    result = await gateway.call_tool(
        _auth(owner_account_id=None), namespaced_tool_name="github.list_issues", arguments={}
    )

    assert result.is_error is True
    assert result.structured_content["error"]["type"] == "mcp_user_key_required"
    assert result.content[0]["text"] == (
        "This key is not owned by a user. Per-user OAuth tools need a user key."
    )
    assert harness.repository.calls == []


async def test_connected_user_token_is_sent_upstream() -> None:
    harness = OAuthHarness()
    transport = _RecordingTransport()
    gateway, server = await _gateway(harness, transport)
    await harness.connect(server)

    result = await gateway.call_tool(
        _auth(), namespaced_tool_name="github.list_issues", arguments={}
    )

    assert result.is_error is False
    assert transport.tokens == ["access-1"]


async def test_rejected_token_is_refreshed_and_the_call_is_retried_once() -> None:
    harness = OAuthHarness()
    transport = _RecordingTransport(reject_tokens={"access-1"})
    gateway, server = await _gateway(harness, transport)
    await harness.connect(server)

    result = await gateway.call_tool(
        _auth(), namespaced_tool_name="github.list_issues", arguments={}
    )

    assert result.is_error is False
    assert transport.tokens == ["access-1", "access-2"]


async def test_revoked_refresh_token_marks_needs_reconnect() -> None:
    harness = OAuthHarness()
    harness.upstream.refresh_behavior = "invalid_grant"
    transport = _RecordingTransport(reject_tokens={"access-1"})
    gateway, server = await _gateway(harness, transport)
    await harness.connect(server)

    result = await gateway.call_tool(
        _auth(), namespaced_tool_name="github.list_issues", arguments={}
    )

    assert result.is_error is True
    error = result.structured_content["error"]
    assert error["reason"] == "needs_reconnect"
    assert error["message"] == "Connect your GitHub account again to use this tool."
    connection = harness.repository.connections[("srv-1", ACCOUNT)]
    assert connection.status == "needs_reconnect"

    again = await gateway.call_tool(
        _auth(), namespaced_tool_name="github.list_issues", arguments={}
    )
    assert again.structured_content["error"]["reason"] == "needs_reconnect"
    assert transport.tokens == ["access-1"]


async def test_non_oauth_server_makes_no_connection_reads() -> None:
    harness = OAuthHarness()
    transport = _RecordingTransport()
    gateway, _ = await _gateway(harness, transport, oauth=False)
    harness.repository.calls.clear()

    result = await gateway.call_tool(
        _auth(owner_account_id=None), namespaced_tool_name="github.list_issues", arguments={}
    )

    assert result.is_error is False
    assert transport.tokens == [None]
    assert harness.repository.calls == []
    assert harness.upstream.requests == []


async def test_chat_tool_message_carries_connect_url_for_the_model() -> None:
    harness = OAuthHarness()
    gateway, _ = await _gateway(harness, _RecordingTransport())
    result = await gateway.call_tool(
        _auth(), namespaced_tool_name="github.list_issues", arguments={}
    )

    content = MCPChatOrchestrator._tool_message_content(  # noqa: SLF001
        {
            "content": result.content,
            "structured_content": result.structured_content,
            "is_error": result.is_error,
        }
    )
    payload = json.loads(content)
    assert payload["error"]["type"] == "mcp_connection_required"
    assert payload["error"]["connect_url"] == f"{BASE_URL}/ui/connect/github"


async def test_post_mcp_tools_call_returns_is_error_with_connect_text(client, test_app) -> None:  # noqa: ANN001
    test_app.state.audit_service = _AuditSink()
    key_record = next(iter(test_app.state._test_repo.records.values()))
    key_record.team_id = "team-ops"
    key_record.organization_id = "org-acme"
    key_record.owner_account_id = ACCOUNT
    harness = OAuthHarness()
    gateway, _ = await _gateway(harness, _RecordingTransport())
    test_app.state.mcp_gateway_service = gateway

    response = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "github.list_issues", "arguments": {}},
        },
    )

    assert response.status_code == 200, response.text
    assert "result" in response.json(), response.text
    result = response.json()["result"]
    assert result["isError"] is True
    assert result["content"][0]["text"].startswith("Connect your GitHub account to use this tool.")
    assert result["structuredContent"]["error"]["connect_url"] == f"{BASE_URL}/ui/connect/github"
