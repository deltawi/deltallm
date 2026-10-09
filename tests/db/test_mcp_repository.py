from __future__ import annotations

import pytest

from src.db.mcp import MCPRepository


class _FakePrisma:
    def __init__(self, responses: list[list[dict]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def query_raw(self, query: str, *params):  # noqa: ANN001, ANN201
        self.calls.append((query, params))
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_list_servers_parses_rows() -> None:
    prisma = _FakePrisma(
        responses=[
            [{"total": 1}],
            [
                {
                    "mcp_server_id": "srv-1",
                    "server_key": "github",
                    "name": "GitHub",
                    "description": "GitHub tools",
                    "owner_scope_type": "organization",
                    "owner_scope_id": "org-main",
                    "transport": "streamable_http",
                    "base_url": "https://mcp.example.com",
                    "enabled": True,
                    "auth_mode": "bearer",
                    "auth_config": {"token": "secret"},
                    "forwarded_headers_allowlist": ["authorization"],
                    "request_timeout_ms": 12000,
                    "capabilities_json": {"tools": [{"name": "search"}]},
                    "capabilities_etag": "etag-1",
                    "capabilities_fetched_at": "2026-03-11T10:00:00Z",
                    "last_health_status": "healthy",
                    "last_health_error": None,
                    "last_health_at": "2026-03-11T10:05:00Z",
                    "last_health_latency_ms": 90,
                    "metadata": {"env": "test"},
                    "created_by_account_id": "acct-1",
                    "created_at": "2026-03-11T09:00:00Z",
                    "updated_at": "2026-03-11T10:05:00Z",
                }
            ],
        ]
    )
    repo = MCPRepository(prisma)

    items, total = await repo.list_servers(search="git", limit=10, offset=0)

    assert total == 1
    assert len(items) == 1
    assert items[0].server_key == "github"
    assert items[0].owner_scope_type == "organization"
    assert items[0].owner_scope_id == "org-main"
    assert items[0].auth_config == {"token": "secret"}
    assert items[0].forwarded_headers_allowlist == ["authorization"]
    assert items[0].capabilities_json == {"tools": [{"name": "search"}]}


@pytest.mark.asyncio
async def test_list_effective_bindings_returns_enabled_rows() -> None:
    prisma = _FakePrisma(
        responses=[
            [
                {
                    "mcp_binding_id": "bind-1",
                    "mcp_server_id": "srv-1",
                    "scope_type": "team",
                    "scope_id": "team-1",
                    "enabled": True,
                    "tool_allowlist": ["search"],
                    "metadata": {"note": "team scope"},
                    "created_at": "2026-03-11T09:00:00Z",
                    "updated_at": "2026-03-11T09:05:00Z",
                }
            ]
        ]
    )
    repo = MCPRepository(prisma)

    rows = await repo.list_effective_bindings(scopes=[("team", "team-1")])

    assert len(rows) == 1
    assert rows[0].scope_type == "team"
    assert rows[0].tool_allowlist == ["search"]
