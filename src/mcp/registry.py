from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from src.db.mcp import MCPRepository, MCPServerBindingRecord, MCPServerRecord, MCPToolPolicyRecord

from .capabilities import NamespacedTool, extract_tool_schemas, namespace_tools
from .models import MCPServerConfig

_SERVER_CACHE_PREFIX = "deltallm:mcp:server:v1"


@dataclass
class _CacheEntry:
    value: dict[str, Any]
    expires_at: float


class MCPRegistryService:
    def __init__(
        self,
        repository: MCPRepository,
        redis_client: Any | None = None,
        *,
        l1_ttl_seconds: int = 30,
        l2_ttl_seconds: int = 300,
    ) -> None:
        self.repository = repository
        self.redis = redis_client
        self.l1_ttl_seconds = max(1, int(l1_ttl_seconds))
        self.l2_ttl_seconds = max(self.l1_ttl_seconds, int(l2_ttl_seconds))
        self._server_capabilities_l1: dict[str, _CacheEntry] = {}
        self._known_server_keys: set[str] = set()

    async def get_server(self, server_id: str) -> MCPServerRecord | None:
        return await self.repository.get_server(server_id)

    async def list_servers(
        self,
        *,
        search: str | None = None,
        enabled: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[MCPServerRecord], int]:
        return await self.repository.list_servers(search=search, enabled=enabled, limit=limit, offset=offset)

    async def get_server_capabilities(self, server: MCPServerRecord) -> dict[str, Any]:
        l1_key = server.server_key
        self._known_server_keys.add(l1_key)
        now = time.monotonic()
        cached = self._server_capabilities_l1.get(l1_key)
        if cached is not None and cached.expires_at > now:
            return dict(cached.value)

        if self.redis is not None:
            raw = await self.redis.get(self._redis_server_key(server.server_key))
            if raw:
                try:
                    payload = json.loads(raw)
                except (TypeError, ValueError):
                    payload = None
                if isinstance(payload, dict):
                    self._server_capabilities_l1[l1_key] = _CacheEntry(value=payload, expires_at=now + self.l1_ttl_seconds)
                    return dict(payload)

        payload = dict(server.capabilities_json or {})
        if payload:
            self._server_capabilities_l1[l1_key] = _CacheEntry(value=payload, expires_at=now + self.l1_ttl_seconds)
        return payload

    async def store_server_capabilities(
        self,
        server_id: str,
        *,
        capabilities: dict[str, Any],
        etag: str | None = None,
    ) -> MCPServerRecord | None:
        updated = await self.repository.update_server_capabilities(
            server_id,
            capabilities_json=capabilities,
            capabilities_etag=etag,
        )
        if updated is not None:
            await self.invalidate_server(updated.server_key)
            await self._populate_cache(updated.server_key, capabilities)
        return updated

    async def record_health(
        self,
        server_id: str,
        *,
        status: str,
        error: str | None,
        latency_ms: int | None,
    ) -> MCPServerRecord | None:
        return await self.repository.record_health_check(
            server_id,
            status=status,
            error=error,
            latency_ms=latency_ms,
        )

    async def list_effective_bindings(self, *, scopes: list[tuple[str, str]]) -> list[MCPServerBindingRecord]:
        return await self.repository.list_effective_bindings(scopes=scopes)

    async def list_effective_tool_policies(
        self,
        *,
        scopes: list[tuple[str, str]],
        server_id: str | None = None,
    ) -> list[MCPToolPolicyRecord]:
        return await self.repository.list_effective_tool_policies(scopes=scopes, server_id=server_id)

    async def list_namespaced_tools(self, server: MCPServerRecord) -> list[NamespacedTool]:
        capabilities = await self.get_server_capabilities(server)
        return namespace_tools(server.server_key, extract_tool_schemas(capabilities))

    async def invalidate_server(self, server_key: str) -> None:
        self._server_capabilities_l1.pop(server_key, None)
        self._known_server_keys.discard(server_key)
        if self.redis is not None:
            await self.redis.delete(self._redis_server_key(server_key))

    async def invalidate_all(self) -> None:
        server_keys = list(self._known_server_keys)
        self._server_capabilities_l1.clear()
        self._known_server_keys.clear()
        if self.redis is not None and server_keys:
            await self.redis.delete(*(self._redis_server_key(server_key) for server_key in server_keys))

    async def _populate_cache(self, server_key: str, capabilities: dict[str, Any]) -> None:
        self._known_server_keys.add(server_key)
        self._server_capabilities_l1[server_key] = _CacheEntry(
            value=dict(capabilities),
            expires_at=time.monotonic() + self.l1_ttl_seconds,
        )
        if self.redis is not None:
            await self.redis.set(self._redis_server_key(server_key), json.dumps(capabilities), ex=self.l2_ttl_seconds)

    @staticmethod
    def _redis_server_key(server_key: str) -> str:
        return f"{_SERVER_CACHE_PREFIX}:{server_key}"


def server_record_to_config(server: MCPServerRecord) -> MCPServerConfig:
    return MCPServerConfig(
        server_id=server.mcp_server_id,
        server_key=server.server_key,
        name=server.name,
        transport=server.transport,
        base_url=server.base_url,
        auth_mode=server.auth_mode,
        auth_config=dict(server.auth_config or {}),
        forwarded_headers_allowlist=list(server.forwarded_headers_allowlist or []),
        request_timeout_ms=server.request_timeout_ms,
    )
