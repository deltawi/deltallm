from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from itertools import count
from time import monotonic
from typing import Any

import httpx

from .auth import build_effective_mcp_upstream_headers
from .exceptions import MCPAuthError, MCPInvalidResponseError, MCPTransportError
from .models import MCPRequestEnvelope, MCPServerConfig, MCPToolCallResult, MCPToolSchema
from .capabilities import extract_tool_schemas
from src.upstream_http import build_upstream_request_timeout

_request_ids = count(1)
MCP_PROTOCOL_VERSION = "2025-11-25"
_MCP_ACCEPT = "application/json, text/event-stream"
_MCP_SESSION_TTL_SECONDS = 30 * 60
_MCP_MAX_SESSION_STATES = 1024


@dataclass
class _MCPSessionState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    session_id: str | None = None
    protocol_version: str = MCP_PROTOCOL_VERSION
    initialized: bool = False
    initialize_result: dict[str, Any] | None = None
    last_used_at: float = field(default_factory=monotonic)


@dataclass(frozen=True)
class _MCPTransportResponse:
    result: dict[str, Any]
    headers: httpx.Headers


class _StaleMCPSessionError(Exception):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__("MCP server rejected session")


class StreamableHTTPMCPClient:
    def __init__(
        self, http_client: httpx.AsyncClient, *, general_settings: Any | None = None
    ) -> None:
        self.http_client = http_client
        self.general_settings = general_settings
        self._sessions: dict[tuple[object, ...], _MCPSessionState] = {}

    async def initialize(
        self,
        server: MCPServerConfig,
        *,
        request_headers: dict[str, str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        key = self._session_key(server, request_headers=request_headers)
        state = self._session_for_key(key)
        if force:
            async with state.lock:
                return await self._initialize_locked_with_retry(
                    server, request_headers=request_headers, state=state
                )
        state = await self._ensure_initialized(server, request_headers=request_headers, key=key)
        if state.initialize_result is None:
            raise MCPTransportError("MCP session initialized without initialize result")
        return state.initialize_result

    async def list_tools(
        self,
        server: MCPServerConfig,
        *,
        request_headers: dict[str, str] | None = None,
    ) -> list[MCPToolSchema]:
        response = await self._send_with_session(
            server,
            request_headers=request_headers,
            envelope=MCPRequestEnvelope(
                method="tools/list", params={}, request_id=next(_request_ids)
            ),
        )
        return extract_tool_schemas(response.result)

    async def call_tool(
        self,
        server: MCPServerConfig,
        *,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        request_headers: dict[str, str] | None = None,
    ) -> MCPToolCallResult:
        response = await self._send_with_session(
            server,
            request_headers=request_headers,
            envelope=MCPRequestEnvelope(
                method="tools/call",
                params={"name": tool_name, "arguments": arguments or {}},
                request_id=next(_request_ids),
            ),
        )
        payload = response.result
        content = payload.get("content")
        structured = payload.get("structuredContent")
        return MCPToolCallResult(
            content=content if isinstance(content, list) else [],
            structured_content=structured if isinstance(structured, dict) else None,
            is_error=bool(payload.get("isError", False)),
            metadata=payload if isinstance(payload, dict) else {},
        )

    async def _send_with_session(
        self,
        server: MCPServerConfig,
        *,
        request_headers: dict[str, str] | None,
        envelope: MCPRequestEnvelope,
    ) -> _MCPTransportResponse:
        key = self._session_key(server, request_headers=request_headers)
        state = await self._ensure_initialized(server, request_headers=request_headers, key=key)
        try:
            return await self._send(
                server,
                envelope,
                request_headers=request_headers,
                state=state,
            )
        except _StaleMCPSessionError as exc:
            await self._clear_session(key, state, stale_session_id=exc.session_id)
            state = await self._ensure_initialized(server, request_headers=request_headers, key=key)
            try:
                return await self._send(
                    server,
                    envelope,
                    request_headers=request_headers,
                    state=state,
                )
            except _StaleMCPSessionError as exc:
                await self._clear_session(key, state, stale_session_id=exc.session_id)
                raise MCPTransportError("MCP server rejected refreshed MCP session") from exc

    async def _ensure_initialized(
        self,
        server: MCPServerConfig,
        *,
        request_headers: dict[str, str] | None,
        key: tuple[object, ...],
    ) -> _MCPSessionState:
        state = self._session_for_key(key)
        if state.initialized and state.initialize_result is not None:
            self._touch_session(state)
            return state
        async with state.lock:
            if not state.initialized or state.initialize_result is None:
                await self._initialize_locked_with_retry(
                    server, request_headers=request_headers, state=state
                )
        return state

    async def _initialize_locked_with_retry(
        self,
        server: MCPServerConfig,
        *,
        request_headers: dict[str, str] | None,
        state: _MCPSessionState,
    ) -> dict[str, Any]:
        last_stale: _StaleMCPSessionError | None = None
        for _ in range(2):
            try:
                return await self._initialize_locked(
                    server, request_headers=request_headers, state=state
                )
            except _StaleMCPSessionError as exc:
                last_stale = exc
                self._reset_session_state_if_current(state, exc.session_id)
        raise MCPTransportError(
            "MCP server rejected initialized notification for MCP session"
        ) from last_stale

    async def _initialize_locked(
        self,
        server: MCPServerConfig,
        *,
        request_headers: dict[str, str] | None,
        state: _MCPSessionState,
    ) -> dict[str, Any]:
        self._reset_session_state(state)
        response = await self._send(
            server,
            MCPRequestEnvelope(
                method="initialize",
                params={
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "deltallm", "version": "0.1.0"},
                },
                request_id=next(_request_ids),
            ),
            request_headers=request_headers,
            state=None,
            include_protocol_header=False,
        )
        session_id = response.headers.get("MCP-Session-Id") or response.headers.get(
            "mcp-session-id"
        )
        if session_id:
            state.session_id = session_id
        negotiated_protocol = response.result.get("protocolVersion")
        if isinstance(negotiated_protocol, str) and negotiated_protocol.strip():
            state.protocol_version = negotiated_protocol.strip()
        try:
            await self._send(
                server,
                MCPRequestEnvelope(method="notifications/initialized"),
                request_headers=request_headers,
                state=state,
                expect_response=False,
            )
        except _StaleMCPSessionError as exc:
            self._reset_session_state_if_current(state, exc.session_id)
            raise
        except Exception:
            self._reset_session_state(state)
            raise
        state.initialized = True
        state.initialize_result = response.result
        self._touch_session(state)
        return response.result

    async def _send(
        self,
        server: MCPServerConfig,
        envelope: MCPRequestEnvelope,
        *,
        request_headers: dict[str, str] | None = None,
        state: _MCPSessionState | None = None,
        include_protocol_header: bool = True,
        expect_response: bool = True,
    ) -> _MCPTransportResponse:
        session_id = state.session_id if state is not None else None
        protocol_version = state.protocol_version if state is not None else MCP_PROTOCOL_VERSION
        headers = self._upstream_headers(server, request_headers=request_headers)
        headers["Content-Type"] = "application/json"
        headers["Accept"] = _MCP_ACCEPT
        if session_id:
            headers["MCP-Session-Id"] = session_id
        if state is not None and include_protocol_header:
            headers["MCP-Protocol-Version"] = protocol_version
        body = {
            "jsonrpc": "2.0",
            "method": envelope.method,
            "params": envelope.params,
        }
        if envelope.request_id is not None:
            body["id"] = envelope.request_id
        try:
            async with self.http_client.stream(
                "POST",
                server.base_url.rstrip("/"),
                json=body,
                headers=headers,
                timeout=build_upstream_request_timeout(
                    self.general_settings,
                    max(1.0, float(server.request_timeout_ms) / 1000.0),
                ),
            ) as response:
                if response.status_code in {401, 403}:
                    raise MCPAuthError(
                        f"MCP server rejected request with status {response.status_code}"
                    )
                if response.status_code == 404 and session_id:
                    raise _StaleMCPSessionError(session_id)
                if response.status_code >= 400:
                    raise MCPTransportError(await _http_error_message(response))
                if not expect_response:
                    return _MCPTransportResponse(result={}, headers=response.headers)
                payload = await _decode_response_payload(response, request_id=envelope.request_id)
                return _MCPTransportResponse(
                    result=_result_from_payload(payload), headers=response.headers
                )
        except httpx.HTTPError as exc:
            raise MCPTransportError(str(exc)) from exc

    def _session_for_key(self, key: tuple[object, ...]) -> _MCPSessionState:
        self._prune_sessions()
        state = self._sessions.get(key)
        if state is None:
            state = _MCPSessionState()
            self._sessions[key] = state
            self._prune_sessions(protected_key=key)
        self._touch_session(state)
        return state

    async def _clear_session(
        self,
        key: tuple[object, ...],
        state: _MCPSessionState,
        *,
        stale_session_id: str | None = None,
    ) -> None:
        async with state.lock:
            if self._sessions.get(key) is state and (
                stale_session_id is None or state.session_id == stale_session_id
            ):
                self._reset_session_state(state)

    @staticmethod
    def _reset_session_state(state: _MCPSessionState) -> None:
        state.session_id = None
        state.protocol_version = MCP_PROTOCOL_VERSION
        state.initialized = False
        state.initialize_result = None
        state.last_used_at = monotonic()

    @staticmethod
    def _reset_session_state_if_current(state: _MCPSessionState, session_id: str) -> None:
        if state.session_id == session_id:
            StreamableHTTPMCPClient._reset_session_state(state)

    @staticmethod
    def _touch_session(state: _MCPSessionState) -> None:
        state.last_used_at = monotonic()

    def _prune_sessions(self, *, protected_key: tuple[object, ...] | None = None) -> None:
        if not self._sessions:
            return
        now = monotonic()
        for key, state in list(self._sessions.items()):
            if key == protected_key:
                continue
            if state.lock.locked():
                continue
            if now - state.last_used_at > _MCP_SESSION_TTL_SECONDS:
                self._sessions.pop(key, None)
        overflow = len(self._sessions) - _MCP_MAX_SESSION_STATES
        if overflow <= 0:
            return
        unlocked = sorted(
            (
                (key, state)
                for key, state in self._sessions.items()
                if key != protected_key and not state.lock.locked()
            ),
            key=lambda item: item[1].last_used_at,
        )
        for key, _state in unlocked[:overflow]:
            self._sessions.pop(key, None)

    def _session_key(
        self,
        server: MCPServerConfig,
        *,
        request_headers: dict[str, str] | None,
    ) -> tuple[object, ...]:
        headers = self._upstream_headers(server, request_headers=request_headers)
        normalized_headers = tuple(
            sorted((key.strip().lower(), value) for key, value in headers.items())
        )
        headers_digest = hashlib.sha256(
            json.dumps(normalized_headers, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return (server.server_id, server.base_url.rstrip("/"), headers_digest)

    @staticmethod
    def _upstream_headers(
        server: MCPServerConfig,
        *,
        request_headers: dict[str, str] | None,
    ) -> dict[str, str]:
        return build_effective_mcp_upstream_headers(
            auth_mode=server.auth_mode,
            auth_config=server.auth_config,
            request_headers=request_headers,
            server_key=server.server_key,
            allowlist=server.forwarded_headers_allowlist,
        )


async def _decode_response_payload(
    response: httpx.Response, *, request_id: str | int | None
) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" in content_type:
        return await _decode_sse_payload(response, request_id=request_id)
    try:
        content = await response.aread()
        payload = json.loads(content)
    except ValueError as exc:
        raise MCPInvalidResponseError("MCP server returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise MCPInvalidResponseError("MCP server returned unexpected response shape")
    return payload


async def _decode_sse_payload(
    response: httpx.Response, *, request_id: str | int | None
) -> dict[str, Any]:
    data_lines: list[str] = []
    async for raw_line in response.aiter_lines():
        line = raw_line.rstrip("\r")
        payload = _maybe_decode_sse_line(line, data_lines=data_lines, request_id=request_id)
        if payload is not None:
            return payload
    payload = _maybe_decode_sse_line("", data_lines=data_lines, request_id=request_id)
    if payload is not None:
        return payload
    raise MCPInvalidResponseError(
        "MCP server event-stream ended before a matching JSON-RPC response"
    )


def _maybe_decode_sse_line(
    line: str,
    *,
    data_lines: list[str],
    request_id: str | int | None,
) -> dict[str, Any] | None:
    if line == "":
        payload = _decode_sse_data(data_lines, request_id=request_id)
        if data_lines:
            data_lines.clear()
        return payload
    if line.startswith(":"):
        return None
    field, separator, value = line.partition(":")
    if separator and field == "data":
        data_lines.append(value[1:] if value.startswith(" ") else value)
    return None


def _decode_sse_data(
    data_lines: list[str], *, request_id: str | int | None
) -> dict[str, Any] | None:
    if not data_lines:
        return None
    data = "\n".join(data_lines).strip()
    if not data:
        return None
    try:
        payload = json.loads(data)
    except ValueError as exc:
        raise MCPInvalidResponseError("MCP server returned invalid JSON in event-stream") from exc
    if not isinstance(payload, dict):
        raise MCPInvalidResponseError("MCP server returned unexpected event-stream payload")
    if request_id is None or payload.get("id") == request_id:
        return payload
    return None


def _result_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("error"), dict):
        message = str(payload["error"].get("message") or "MCP server returned an error")
        raise MCPTransportError(message)
    result = payload.get("result")
    if not isinstance(result, dict):
        raise MCPInvalidResponseError("MCP server response missing result payload")
    return result


async def _http_error_message(response: httpx.Response) -> str:
    try:
        content = await response.aread()
        payload = json.loads(content)
    except ValueError:
        payload = None
    message = None
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        message = payload["error"].get("message")
    return (
        f"MCP server returned status {response.status_code}: {message}"
        if message
        else f"MCP server returned status {response.status_code}"
    )
