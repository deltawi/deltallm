from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.db.mcp import MCPRepository, MCPServerRecord
from src.mcp.registry import MCPRegistryService


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _server(**overrides: object) -> MCPServerRecord:
    values = {
        "mcp_server_id": "mcp-1",
        "server_key": "github",
        "name": "GitHub",
        "description": None,
        "transport": "streamable_http",
        "base_url": "https://mcp.example.com",
        "enabled": True,
        "auth_mode": "none",
        "auth_config": {},
        "forwarded_headers_allowlist": [],
        "request_timeout_ms": 5000,
        "capabilities_json": None,
        "capabilities_etag": None,
        "capabilities_fetched_at": None,
        "last_health_status": None,
        "last_health_error": None,
        "last_health_latency_ms": None,
        "last_health_at": None,
        "metadata": None,
        "created_by_account_id": None,
        "created_at": _utcnow(),
        "updated_at": _utcnow(),
    }
    values.update(overrides)
    return MCPServerRecord(**values)


class _FakeRepository(MCPRepository):
    def __init__(self) -> None:
        super().__init__(prisma_client=None)
        self.server = _server()

    async def get_server(self, server_id: str):  # noqa: ANN201
        if server_id != self.server.mcp_server_id:
            return None
        return self.server

    async def update_server_capabilities(self, server_id: str, *, capabilities_json, capabilities_etag=None):  # noqa: ANN001, ANN201
        if server_id != self.server.mcp_server_id:
            return None
        self.server = _server(
            capabilities_json=capabilities_json,
            capabilities_etag=capabilities_etag,
            capabilities_fetched_at=_utcnow(),
        )
        return self.server


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):  # noqa: ANN201
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.store[key] = value

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)


@pytest.mark.asyncio
async def test_store_server_capabilities_populates_l1_and_l2_cache() -> None:
    repository = _FakeRepository()
    redis = _FakeRedis()
    registry = MCPRegistryService(repository, redis_client=redis)

    updated = await registry.store_server_capabilities(repository.server.mcp_server_id, capabilities={"tools": [{"name": "search"}]})

    assert updated is not None
    cached = await registry.get_server_capabilities(updated)
    assert cached == {"tools": [{"name": "search"}]}
    assert redis.store


@pytest.mark.asyncio
async def test_get_server_capabilities_uses_redis_cache_when_available() -> None:
    repository = _FakeRepository()
    redis = _FakeRedis()
    registry = MCPRegistryService(repository, redis_client=redis)
    redis.store["deltallm:mcp:server:v1:github"] = '{"tools":[{"name":"search"}]}'

    cached = await registry.get_server_capabilities(repository.server)

    assert cached == {"tools": [{"name": "search"}]}


@pytest.mark.asyncio
async def test_invalidate_server_clears_l1_and_l2_cache() -> None:
    repository = _FakeRepository()
    redis = _FakeRedis()
    registry = MCPRegistryService(repository, redis_client=redis)
    await registry.store_server_capabilities(repository.server.mcp_server_id, capabilities={"tools": [{"name": "search"}]})

    await registry.invalidate_server("github")

    assert not registry._server_capabilities_l1
    assert redis.store == {}


@pytest.mark.asyncio
async def test_invalidate_all_clears_known_redis_entries() -> None:
    repository = _FakeRepository()
    redis = _FakeRedis()
    registry = MCPRegistryService(repository, redis_client=redis)
    await registry.store_server_capabilities(repository.server.mcp_server_id, capabilities={"tools": [{"name": "search"}]})

    await registry.invalidate_all()

    assert not registry._server_capabilities_l1
    assert redis.store == {}
