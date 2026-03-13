from __future__ import annotations

from itertools import count
from typing import Any

import httpx

from .auth import build_forwarded_headers, build_server_headers
from .exceptions import MCPAuthError, MCPInvalidResponseError, MCPTransportError
from .models import MCPRequestEnvelope, MCPServerConfig, MCPToolCallResult, MCPToolSchema
from .capabilities import extract_tool_schemas

_request_ids = count(1)


class StreamableHTTPMCPClient:
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self.http_client = http_client

    async def initialize(
        self,
        server: MCPServerConfig,
        *,
        request_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return await self._send(
            server,
            MCPRequestEnvelope(
                method="initialize",
                params={"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "deltallm", "version": "0.1.0"}},
                request_id=next(_request_ids),
            ),
            request_headers=request_headers,
        )

    async def list_tools(
        self,
        server: MCPServerConfig,
        *,
        request_headers: dict[str, str] | None = None,
    ) -> list[MCPToolSchema]:
        payload = await self._send(
            server,
            MCPRequestEnvelope(method="tools/list", params={}, request_id=next(_request_ids)),
            request_headers=request_headers,
        )
        return extract_tool_schemas(payload)

    async def call_tool(
        self,
        server: MCPServerConfig,
        *,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        request_headers: dict[str, str] | None = None,
    ) -> MCPToolCallResult:
        payload = await self._send(
            server,
            MCPRequestEnvelope(
                method="tools/call",
                params={"name": tool_name, "arguments": arguments or {}},
                request_id=next(_request_ids),
            ),
            request_headers=request_headers,
        )
        content = payload.get("content")
        structured = payload.get("structuredContent")
        return MCPToolCallResult(
            content=content if isinstance(content, list) else [],
            structured_content=structured if isinstance(structured, dict) else None,
            is_error=bool(payload.get("isError", False)),
            metadata=payload if isinstance(payload, dict) else {},
        )

    async def _send(
        self,
        server: MCPServerConfig,
        envelope: MCPRequestEnvelope,
        *,
        request_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **build_server_headers(auth_mode=server.auth_mode, auth_config=server.auth_config),
            **build_forwarded_headers(
                request_headers=request_headers,
                server_key=server.server_key,
                allowlist=server.forwarded_headers_allowlist,
            ),
        }
        body = {
            "jsonrpc": "2.0",
            "id": envelope.request_id,
            "method": envelope.method,
            "params": envelope.params,
        }
        try:
            response = await self.http_client.post(
                server.base_url.rstrip("/"),
                json=body,
                headers=headers,
                timeout=max(1.0, float(server.request_timeout_ms) / 1000.0),
            )
        except httpx.HTTPError as exc:
            raise MCPTransportError(str(exc)) from exc
        if response.status_code in {401, 403}:
            raise MCPAuthError(f"MCP server rejected request with status {response.status_code}")
        if response.status_code >= 400:
            raise MCPTransportError(f"MCP server returned status {response.status_code}")
        if "text/event-stream" in response.headers.get("content-type", "").lower():
            raise MCPInvalidResponseError("MCP server returned unsupported event-stream response")
        try:
            payload = response.json()
        except ValueError as exc:
            raise MCPInvalidResponseError("MCP server returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise MCPInvalidResponseError("MCP server returned unexpected response shape")
        if isinstance(payload.get("error"), dict):
            message = str(payload["error"].get("message") or "MCP server returned an error")
            raise MCPTransportError(message)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise MCPInvalidResponseError("MCP server response missing result payload")
        return result
