from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from src.cache import CacheBackend, CacheEntry

from .auth import build_forwarded_headers
from .models import MCPServerConfig, MCPToolCallResult


class MCPToolResultCache:
    def __init__(self, backend: CacheBackend | None) -> None:
        self.backend = backend

    async def get(
        self,
        *,
        server: MCPServerConfig,
        tool_name: str,
        arguments: dict[str, Any] | None,
        request_headers: dict[str, str] | None,
        auth_api_key: str | None,
    ) -> MCPToolCallResult | None:
        if self.backend is None:
            return None
        entry = await self.backend.get(
            self._cache_key(
                server=server,
                tool_name=tool_name,
                arguments=arguments,
                request_headers=request_headers,
                auth_api_key=auth_api_key,
            )
        )
        if entry is None:
            return None
        payload = entry.response if isinstance(entry.response, dict) else {}
        return MCPToolCallResult(
            content=payload.get("content") if isinstance(payload.get("content"), list) else [],
            structured_content=payload.get("structured_content")
            if isinstance(payload.get("structured_content"), dict)
            else None,
            is_error=bool(payload.get("is_error", False)),
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        )

    async def set(
        self,
        *,
        server: MCPServerConfig,
        tool_name: str,
        arguments: dict[str, Any] | None,
        request_headers: dict[str, str] | None,
        auth_api_key: str | None,
        result: MCPToolCallResult,
        ttl_seconds: int,
    ) -> None:
        if self.backend is None or ttl_seconds <= 0 or result.is_error:
            return
        await self.backend.set(
            self._cache_key(
                server=server,
                tool_name=tool_name,
                arguments=arguments,
                request_headers=request_headers,
                auth_api_key=auth_api_key,
            ),
            CacheEntry(
                response={
                    "content": result.content,
                    "structured_content": result.structured_content,
                    "is_error": result.is_error,
                    "metadata": result.metadata,
                },
                model=f"mcp:{server.server_key}.{tool_name}",
                cached_at=time.time(),
                ttl=ttl_seconds,
            ),
            ttl=ttl_seconds,
        )

    @staticmethod
    def _cache_key(
        *,
        server: MCPServerConfig,
        tool_name: str,
        arguments: dict[str, Any] | None,
        request_headers: dict[str, str] | None,
        auth_api_key: str | None,
    ) -> str:
        forwarded_headers = build_forwarded_headers(
            request_headers=request_headers,
            server_key=server.server_key,
            allowlist=server.forwarded_headers_allowlist,
        )
        payload = {
            "server_key": server.server_key,
            "tool_name": tool_name,
            "arguments": arguments or {},
            "api_key": auth_api_key or "",
            "forwarded_headers": forwarded_headers,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return f"mcp_tool:{digest}"
