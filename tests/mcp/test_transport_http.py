from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from collections.abc import Callable
import json
from time import monotonic

import httpx
import pytest

from src.mcp.exceptions import MCPAuthError, MCPInvalidResponseError, MCPTransportError
from src.mcp.models import MCPServerConfig
from src.mcp.transport_http import StreamableHTTPMCPClient


class _FakeStreamContext:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response

    async def __aenter__(self) -> httpx.Response:
        return self.response

    async def __aexit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        return None


class _StreamingSSEBody(httpx.AsyncByteStream):
    def __init__(self, lines: list[str], *, tail_event: asyncio.Event | None = None) -> None:
        self.lines = lines
        self.tail_event = tail_event

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for line in self.lines:
            yield line.encode("utf-8")
        if self.tail_event is not None:
            await self.tail_event.wait()


class _FakeHTTPClient:
    def __init__(
        self,
        response: httpx.Response | Callable[[dict[str, object]], httpx.Response] | None = None,
        *,
        responses: list[httpx.Response | Callable[[dict[str, object]], httpx.Response]]
        | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.responses = list(responses or ([response] if response is not None else []))
        self.exc = exc
        self.calls: list[dict[str, object]] = []

    def stream(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: object,
    ) -> _FakeStreamContext:
        self.calls.append(
            {"method": method, "url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        if self.exc is not None:
            raise self.exc
        assert self.responses
        response = self.responses.pop(0)
        return _FakeStreamContext(response(json) if callable(response) else response)

    async def post(
        self, url: str, *, json: dict[str, object], headers: dict[str, str], timeout: object
    ) -> httpx.Response:
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.exc is not None:
            raise self.exc
        assert self.responses
        response = self.responses.pop(0)
        return response(json) if callable(response) else response


def _response(
    status_code: int, payload: dict[str, object], *, headers: dict[str, str] | None = None
) -> httpx.Response:
    request = httpx.Request("POST", "https://mcp.example.com")
    return httpx.Response(status_code, json=payload, headers=headers, request=request)


def _accepted_response() -> httpx.Response:
    request = httpx.Request("POST", "https://mcp.example.com")
    return httpx.Response(202, request=request)


def _sse_response(
    status_code: int,
    payload: dict[str, object],
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    request = httpx.Request("POST", "https://mcp.example.com")
    merged_headers = {"content-type": "text/event-stream", **(headers or {})}
    text = f"event: message\ndata: {json.dumps(payload)}\n\n"
    return httpx.Response(status_code, text=text, headers=merged_headers, request=request)


def _open_sse_response(payload: dict[str, object], *, tail_event: asyncio.Event) -> httpx.Response:
    request = httpx.Request("POST", "https://mcp.example.com")
    lines = ["event: message\n", f"data: {json.dumps(payload)}\n", "\n"]
    return httpx.Response(
        200,
        stream=_StreamingSSEBody(lines, tail_event=tail_event),
        headers={"content-type": "text/event-stream"},
        request=request,
    )


def _sse_result_response(
    result: dict[str, object],
    *,
    headers: dict[str, str] | None = None,
) -> Callable[[dict[str, object]], httpx.Response]:
    def _factory(body: dict[str, object]) -> httpx.Response:
        return _sse_response(
            200, {"jsonrpc": "2.0", "id": body.get("id"), "result": result}, headers=headers
        )

    return _factory


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
        responses=[
            _response(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"protocolVersion": "2025-11-25", "serverInfo": {"name": "github"}},
                },
                headers={"MCP-Session-Id": "session-1"},
            ),
            _accepted_response(),
        ]
    )
    general_settings = type("GeneralSettings", (), {"upstream_http_pool_timeout_seconds": 2})()
    transport = StreamableHTTPMCPClient(client, general_settings=general_settings)  # type: ignore[arg-type]

    payload = await transport.initialize(
        _server(),
        request_headers={"x-deltallm-mcp-github-authorization": "Bearer forwarded"},
    )

    assert payload == {"protocolVersion": "2025-11-25", "serverInfo": {"name": "github"}}
    assert len(client.calls) == 2
    call = client.calls[0]
    assert call["url"] == "https://mcp.example.com"
    timeout = call["timeout"]
    assert getattr(timeout, "read") == 5.0
    assert getattr(timeout, "pool") == 2.0
    headers = call["headers"]
    assert isinstance(headers, dict)
    assert headers["Accept"] == "application/json, text/event-stream"
    assert headers["Authorization"] == "Bearer secret-token"
    assert headers["authorization"] == "Bearer forwarded"
    assert "MCP-Session-Id" not in headers
    assert "MCP-Protocol-Version" not in headers
    body = call["json"]
    assert isinstance(body, dict)
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "initialize"
    assert body["params"]["protocolVersion"] == "2025-11-25"  # type: ignore[index]

    initialized_call = client.calls[1]
    initialized_headers = initialized_call["headers"]
    assert isinstance(initialized_headers, dict)
    assert initialized_headers["MCP-Session-Id"] == "session-1"
    assert initialized_headers["MCP-Protocol-Version"] == "2025-11-25"
    initialized_body = initialized_call["json"]
    assert isinstance(initialized_body, dict)
    assert initialized_body["method"] == "notifications/initialized"
    assert "id" not in initialized_body


@pytest.mark.asyncio
async def test_initialize_reuses_cached_session_result() -> None:
    client = _FakeHTTPClient(
        responses=[
            _response(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"protocolVersion": "2025-11-25", "serverInfo": {"name": "github"}},
                },
                headers={"MCP-Session-Id": "session-1"},
            ),
            _accepted_response(),
        ]
    )
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]
    server = _server(auth_mode="none", auth_config={})

    first = await transport.initialize(server)
    second = await transport.initialize(server)

    assert first == second
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_initialize_force_refreshes_session() -> None:
    client = _FakeHTTPClient(
        responses=[
            _response(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"protocolVersion": "2025-11-25", "serverInfo": {"name": "old"}},
                },
                headers={"MCP-Session-Id": "session-1"},
            ),
            _accepted_response(),
            _response(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"protocolVersion": "2025-11-25", "serverInfo": {"name": "new"}},
                },
                headers={"MCP-Session-Id": "session-2"},
            ),
            _accepted_response(),
        ]
    )
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]
    server = _server(auth_mode="none", auth_config={})

    first = await transport.initialize(server)
    second = await transport.initialize(server, force=True)

    assert first["serverInfo"] == {"name": "old"}
    assert second["serverInfo"] == {"name": "new"}
    assert len(client.calls) == 4
    refreshed_initialized_headers = client.calls[3]["headers"]
    assert isinstance(refreshed_initialized_headers, dict)
    assert refreshed_initialized_headers["MCP-Session-Id"] == "session-2"


@pytest.mark.asyncio
async def test_mcp_transport_headers_override_header_map_config() -> None:
    client = _FakeHTTPClient(
        responses=[
            _response(
                200,
                {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25"}},
                headers={"MCP-Session-Id": "session-1"},
            ),
            _accepted_response(),
        ]
    )
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]
    server = _server(
        auth_mode="header_map",
        auth_config={
            "headers": {
                "Accept": "application/json",
                "Authorization": "Bearer upstream",
                "Content-Type": "text/plain",
                "MCP-Protocol-Version": "1900-01-01",
                "MCP-Session-Id": "bad-session",
            }
        },
    )

    await transport.initialize(server)

    initialize_headers = client.calls[0]["headers"]
    initialized_headers = client.calls[1]["headers"]
    assert isinstance(initialize_headers, dict)
    assert isinstance(initialized_headers, dict)
    assert initialize_headers["Accept"] == "application/json, text/event-stream"
    assert initialize_headers["Authorization"] == "Bearer upstream"
    assert initialize_headers["Content-Type"] == "application/json"
    assert "MCP-Protocol-Version" not in initialize_headers
    assert "MCP-Session-Id" not in initialize_headers
    assert initialized_headers["Accept"] == "application/json, text/event-stream"
    assert initialized_headers["Content-Type"] == "application/json"
    assert initialized_headers["MCP-Protocol-Version"] == "2025-11-25"
    assert initialized_headers["MCP-Session-Id"] == "session-1"


@pytest.mark.asyncio
async def test_forwarded_headers_cannot_override_mcp_transport_headers() -> None:
    client = _FakeHTTPClient(
        responses=[
            _response(
                200,
                {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25"}},
                headers={"MCP-Session-Id": "session-1"},
            ),
            _accepted_response(),
            _response(200, {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}),
        ]
    )
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]
    server = _server(
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[
            "accept",
            "authorization",
            "content-type",
            "mcp-protocol-version",
            "mcp-session-id",
        ],
    )

    await transport.list_tools(
        server,
        request_headers={
            "x-deltallm-mcp-github-accept": "application/json",
            "x-deltallm-mcp-github-authorization": "Bearer forwarded",
            "x-deltallm-mcp-github-content-type": "text/plain",
            "x-deltallm-mcp-github-mcp-protocol-version": "1900-01-01",
            "x-deltallm-mcp-github-mcp-session-id": "bad-session",
        },
    )

    list_headers = client.calls[2]["headers"]
    assert isinstance(list_headers, dict)
    assert list_headers["Accept"] == "application/json, text/event-stream"
    assert list_headers["authorization"] == "Bearer forwarded"
    assert list_headers["Content-Type"] == "application/json"
    assert list_headers["MCP-Protocol-Version"] == "2025-11-25"
    assert list_headers["MCP-Session-Id"] == "session-1"
    assert "accept" not in list_headers
    assert "content-type" not in list_headers
    assert "mcp-protocol-version" not in list_headers
    assert "mcp-session-id" not in list_headers


@pytest.mark.asyncio
async def test_list_tools_parses_tools_from_result() -> None:
    client = _FakeHTTPClient(
        responses=[
            _response(
                200,
                {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25"}},
                headers={"MCP-Session-Id": "session-tools"},
            ),
            _accepted_response(),
            _response(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "tools": [
                            {
                                "name": "search",
                                "description": "Search",
                                "inputSchema": {"type": "object"},
                            },
                        ]
                    },
                },
            ),
        ]
    )
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]

    tools = await transport.list_tools(_server(auth_mode="none", auth_config={}))

    assert len(tools) == 1
    assert tools[0].name == "search"
    assert tools[0].input_schema == {"type": "object"}
    list_headers = client.calls[2]["headers"]
    assert isinstance(list_headers, dict)
    assert list_headers["MCP-Session-Id"] == "session-tools"
    assert list_headers["MCP-Protocol-Version"] == "2025-11-25"


@pytest.mark.asyncio
async def test_call_tool_returns_content_and_structured_content() -> None:
    client = _FakeHTTPClient(
        responses=[
            _response(
                200,
                {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25"}},
                headers={"MCP-Session-Id": "session-call"},
            ),
            _accepted_response(),
            _response(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "content": [{"type": "text", "text": "ok"}],
                        "structuredContent": {"status": "ok"},
                        "isError": False,
                    },
                },
            ),
        ]
    )
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]

    result = await transport.call_tool(_server(), tool_name="search", arguments={"q": "test"})

    assert result.content == [{"type": "text", "text": "ok"}]
    assert result.structured_content == {"status": "ok"}
    assert result.is_error is False
    call_body = client.calls[2]["json"]
    assert isinstance(call_body, dict)
    assert call_body["method"] == "tools/call"
    assert call_body["params"] == {"name": "search", "arguments": {"q": "test"}}


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
async def test_send_parses_event_stream_response() -> None:
    client = _FakeHTTPClient(
        responses=[
            _sse_result_response(
                {"protocolVersion": "2025-11-25", "serverInfo": {"name": "github"}},
                headers={"MCP-Session-Id": "session-sse"},
            ),
            _accepted_response(),
        ]
    )
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]

    payload = await transport.initialize(_server())

    assert payload["serverInfo"] == {"name": "github"}
    initialized_headers = client.calls[1]["headers"]
    assert isinstance(initialized_headers, dict)
    assert initialized_headers["MCP-Session-Id"] == "session-sse"


@pytest.mark.asyncio
async def test_send_returns_from_open_event_stream_after_matching_response() -> None:
    tail_event = asyncio.Event()

    def _open_initialize_response(body: dict[str, object]) -> httpx.Response:
        return _open_sse_response(
            {"jsonrpc": "2.0", "id": body.get("id"), "result": {"protocolVersion": "2025-11-25"}},
            tail_event=tail_event,
        )

    client = _FakeHTTPClient(responses=[_open_initialize_response, _accepted_response()])
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]

    payload = await asyncio.wait_for(transport.initialize(_server()), timeout=1)

    assert payload == {"protocolVersion": "2025-11-25"}
    assert tail_event.is_set() is False


@pytest.mark.asyncio
async def test_send_rejects_missing_result_payload() -> None:
    client = _FakeHTTPClient(_response(200, {"jsonrpc": "2.0", "id": 1}))
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]

    with pytest.raises(MCPInvalidResponseError):
        await transport.initialize(_server())


@pytest.mark.asyncio
async def test_initialize_does_not_cache_initialized_state_when_initialized_notification_fails() -> (
    None
):
    client = _FakeHTTPClient(
        responses=[
            _response(
                200,
                {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25"}},
                headers={"MCP-Session-Id": "failed-session"},
            ),
            _response(
                500,
                {
                    "jsonrpc": "2.0",
                    "id": "server-error",
                    "error": {"message": "notification failed"},
                },
            ),
            _response(
                200,
                {"jsonrpc": "2.0", "id": 2, "result": {"protocolVersion": "2025-11-25"}},
                headers={"MCP-Session-Id": "fresh-session"},
            ),
            _accepted_response(),
            _response(200, {"jsonrpc": "2.0", "id": 3, "result": {"tools": []}}),
        ]
    )
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]
    server = _server(auth_mode="none", auth_config={})

    with pytest.raises(MCPTransportError, match="notification failed"):
        await transport.initialize(server)

    tools = await transport.list_tools(server)

    assert tools == []
    second_attempt_body = client.calls[2]["json"]
    assert isinstance(second_attempt_body, dict)
    assert second_attempt_body["method"] == "initialize"


@pytest.mark.asyncio
async def test_initialize_retries_once_when_initialized_notification_rejects_session() -> None:
    client = _FakeHTTPClient(
        responses=[
            _response(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"protocolVersion": "2025-11-25", "serverInfo": {"name": "first"}},
                },
                headers={"MCP-Session-Id": "stale-session"},
            ),
            _response(
                404,
                {"jsonrpc": "2.0", "id": "server-error", "error": {"message": "session expired"}},
            ),
            _response(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"protocolVersion": "2025-11-25", "serverInfo": {"name": "fresh"}},
                },
                headers={"MCP-Session-Id": "fresh-session"},
            ),
            _accepted_response(),
        ]
    )
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]

    payload = await transport.initialize(_server(auth_mode="none", auth_config={}))

    assert payload["serverInfo"] == {"name": "fresh"}
    assert len(client.calls) == 4
    first_initialized_headers = client.calls[1]["headers"]
    second_initialized_headers = client.calls[3]["headers"]
    assert isinstance(first_initialized_headers, dict)
    assert isinstance(second_initialized_headers, dict)
    assert first_initialized_headers["MCP-Session-Id"] == "stale-session"
    assert second_initialized_headers["MCP-Session-Id"] == "fresh-session"


@pytest.mark.asyncio
async def test_initialize_maps_second_initialized_notification_stale_session() -> None:
    client = _FakeHTTPClient(
        responses=[
            _response(
                200,
                {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25"}},
                headers={"MCP-Session-Id": "stale-session"},
            ),
            _response(
                404,
                {"jsonrpc": "2.0", "id": "server-error", "error": {"message": "session expired"}},
            ),
            _response(
                200,
                {"jsonrpc": "2.0", "id": 2, "result": {"protocolVersion": "2025-11-25"}},
                headers={"MCP-Session-Id": "still-stale-session"},
            ),
            _response(
                404, {"jsonrpc": "2.0", "id": "server-error", "error": {"message": "still expired"}}
            ),
        ]
    )
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]

    with pytest.raises(MCPTransportError, match="initialized notification"):
        await transport.initialize(_server(auth_mode="none", auth_config={}))


def test_session_cache_prunes_expired_forwarded_auth_sessions() -> None:
    transport = StreamableHTTPMCPClient(_FakeHTTPClient())  # type: ignore[arg-type]
    server = _server(
        auth_mode="none", auth_config={}, forwarded_headers_allowlist=["authorization"]
    )
    first_headers = {"x-deltallm-mcp-github-authorization": "Bearer first"}
    second_headers = {"x-deltallm-mcp-github-authorization": "Bearer second"}
    first_key = transport._session_key(server, request_headers=first_headers)  # noqa: SLF001
    first_state = transport._session_for_key(first_key)  # noqa: SLF001
    first_state.last_used_at = monotonic() - 31 * 60

    second_key = transport._session_key(server, request_headers=second_headers)  # noqa: SLF001
    transport._session_for_key(second_key)  # noqa: SLF001

    assert first_key not in transport._sessions  # noqa: SLF001
    assert second_key in transport._sessions  # noqa: SLF001


@pytest.mark.asyncio
async def test_clear_session_only_resets_matching_stale_session() -> None:
    transport = StreamableHTTPMCPClient(_FakeHTTPClient())  # type: ignore[arg-type]
    server = _server(auth_mode="none", auth_config={})
    key = transport._session_key(server, request_headers=None)  # noqa: SLF001
    state = transport._session_for_key(key)  # noqa: SLF001
    state.session_id = "fresh-session"
    state.protocol_version = "2025-11-25"
    state.initialized = True
    state.initialize_result = {"protocolVersion": "2025-11-25"}

    await transport._clear_session(key, state, stale_session_id="stale-session")  # noqa: SLF001

    assert state.session_id == "fresh-session"
    assert state.initialized is True
    assert state.initialize_result == {"protocolVersion": "2025-11-25"}

    await transport._clear_session(key, state, stale_session_id="fresh-session")  # noqa: SLF001

    assert state.session_id is None
    assert state.initialized is False
    assert state.initialize_result is None


@pytest.mark.asyncio
async def test_send_reinitializes_once_after_stale_session() -> None:
    client = _FakeHTTPClient(
        responses=[
            _response(
                200,
                {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25"}},
                headers={"MCP-Session-Id": "stale-session"},
            ),
            _accepted_response(),
            _response(
                404,
                {"jsonrpc": "2.0", "id": "server-error", "error": {"message": "session expired"}},
            ),
            _response(
                200,
                {"jsonrpc": "2.0", "id": 2, "result": {"protocolVersion": "2025-11-25"}},
                headers={"MCP-Session-Id": "fresh-session"},
            ),
            _accepted_response(),
            _response(200, {"jsonrpc": "2.0", "id": 3, "result": {"tools": []}}),
        ]
    )
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]

    tools = await transport.list_tools(_server(auth_mode="none", auth_config={}))

    assert tools == []
    stale_list_headers = client.calls[2]["headers"]
    assert isinstance(stale_list_headers, dict)
    assert stale_list_headers["MCP-Session-Id"] == "stale-session"
    retried_list_headers = client.calls[5]["headers"]
    assert isinstance(retried_list_headers, dict)
    assert retried_list_headers["MCP-Session-Id"] == "fresh-session"


@pytest.mark.asyncio
async def test_send_maps_second_stale_session_to_transport_error() -> None:
    client = _FakeHTTPClient(
        responses=[
            _response(
                200,
                {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25"}},
                headers={"MCP-Session-Id": "stale-session"},
            ),
            _accepted_response(),
            _response(
                404,
                {"jsonrpc": "2.0", "id": "server-error", "error": {"message": "session expired"}},
            ),
            _response(
                200,
                {"jsonrpc": "2.0", "id": 2, "result": {"protocolVersion": "2025-11-25"}},
                headers={"MCP-Session-Id": "fresh-session"},
            ),
            _accepted_response(),
            _response(
                404, {"jsonrpc": "2.0", "id": "server-error", "error": {"message": "still expired"}}
            ),
        ]
    )
    transport = StreamableHTTPMCPClient(client)  # type: ignore[arg-type]

    with pytest.raises(MCPTransportError, match="refreshed MCP session"):
        await transport.list_tools(_server(auth_mode="none", auth_config={}))
