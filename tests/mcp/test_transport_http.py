from __future__ import annotations

import httpx
import pytest

from src.mcp.exceptions import MCPAuthError, MCPInvalidResponseError, MCPTransportError
from src.mcp.models import MCPServerConfig
from src.mcp.transport_http import StreamableHTTPMCPClient


class _FakeHTTPClient:
    def __init__(self, response: httpx.Response | None = None, *, exc: Exception | None = None) -> None:
        self.response = response
        self.exc = exc
        self.calls: list[dict[str, object]] = []

    async def post(self, url: str, *, json: dict[str, object], headers: dict[str, str], timeout: float) -> httpx.Response:
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.exc is not None:
            raise self.exc
        assert self.response is not None
        return self.response


def _response(status_code: int, payload: dict[str, object]) -> httpx.Response:
    request = httpx.Request("POST", "https://mcp.example.com")
    return httpx.Response(status_code, json=payload, request=request)


def _server(**overrides: object) -> MCPServerConfig:
    values = {
        "server_id": "mcp-1",
        "server_key": "github",
        "name": "GitHub",
        "transport": "streamable_http",
        "base_url": "https://mcp.example.com",
        "auth_mode": "bearer",
        "auth_config": {"token": "secret-token"},
        "forwarded_headers_allowlist": ["authorization"],
        "request_timeout_ms": 5000,
    }
    values.update(overrides)
    return MCPServerConfig(
        **values,
    )


@pytest.mark.asyncio
async def test_initialize_sends_jsonrpc_request_and_returns_result() -> None:
    client = _FakeHTTPClient(
        _response(200, {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "github"}}})
    )
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]

    payload = await transport.initialize(
        _server(),
        request_headers={"x-deltallm-mcp-github-authorization": "Bearer forwarded"},
    )

    assert payload == {"serverInfo": {"name": "github"}}
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == "https://mcp.example.com"
    assert call["timeout"] == 5.0
    headers = call["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer secret-token"
    assert headers["authorization"] == "Bearer forwarded"
    body = call["json"]
    assert isinstance(body, dict)
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "initialize"


@pytest.mark.asyncio
async def test_list_tools_parses_tools_from_result() -> None:
    client = _FakeHTTPClient(
        _response(
            200,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "tools": [
                        {"name": "search", "description": "Search", "inputSchema": {"type": "object"}},
                    ]
                },
            },
        )
    )
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]

    tools = await transport.list_tools(_server(auth_mode="none", auth_config={}))

    assert len(tools) == 1
    assert tools[0].name == "search"
    assert tools[0].input_schema == {"type": "object"}


@pytest.mark.asyncio
async def test_call_tool_returns_content_and_structured_content() -> None:
    client = _FakeHTTPClient(
        _response(
            200,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [{"type": "text", "text": "ok"}],
                    "structuredContent": {"status": "ok"},
                    "isError": False,
                },
            },
        )
    )
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]

    result = await transport.call_tool(_server(), tool_name="search", arguments={"q": "test"})

    assert result.content == [{"type": "text", "text": "ok"}]
    assert result.structured_content == {"status": "ok"}
    assert result.is_error is False


@pytest.mark.asyncio
async def test_send_maps_auth_failure_to_mcp_auth_error() -> None:
    client = _FakeHTTPClient(_response(401, {"error": "unauthorized"}))
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]

    with pytest.raises(MCPAuthError):
        await transport.initialize(_server())


@pytest.mark.asyncio
async def test_send_maps_non_auth_http_failure_to_transport_error() -> None:
    client = _FakeHTTPClient(_response(503, {"error": "unavailable"}))
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]

    with pytest.raises(MCPTransportError, match="status 503"):
        await transport.initialize(_server())


@pytest.mark.asyncio
async def test_send_maps_httpx_failure_to_transport_error() -> None:
    request = httpx.Request("POST", "https://mcp.example.com")
    client = _FakeHTTPClient(exc=httpx.ReadTimeout("timeout", request=request))
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]

    with pytest.raises(MCPTransportError):
        await transport.initialize(_server())


@pytest.mark.asyncio
async def test_send_rejects_event_stream_response() -> None:
    request = httpx.Request("POST", "https://mcp.example.com")
    response = httpx.Response(200, text="event: message\n\ndata: {}\n\n", headers={"content-type": "text/event-stream"}, request=request)
    client = _FakeHTTPClient(response)
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]

    with pytest.raises(MCPInvalidResponseError, match="event-stream"):
        await transport.initialize(_server())


@pytest.mark.asyncio
async def test_send_rejects_missing_result_payload() -> None:
    client = _FakeHTTPClient(_response(200, {"jsonrpc": "2.0", "id": 1}))
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]

    with pytest.raises(MCPInvalidResponseError):
        await transport.initialize(_server())
