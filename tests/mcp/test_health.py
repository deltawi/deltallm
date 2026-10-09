from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.db.mcp import MCPRepository, MCPServerRecord
from src.mcp.health import MCPHealthProbe
from src.mcp.registry import MCPRegistryService


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _server() -> MCPServerRecord:
    return MCPServerRecord(
        mcp_server_id="mcp-1",
        server_key="github",
        name="GitHub",
        description=None,
        transport="streamable_http",
        base_url="https://mcp.example.com",
        enabled=True,
        auth_mode="none",
        auth_config={},
        forwarded_headers_allowlist=[],
        request_timeout_ms=5000,
        capabilities_json=None,
        capabilities_etag=None,
        capabilities_fetched_at=None,
        last_health_status=None,
        last_health_error=None,
        last_health_latency_ms=None,
        last_health_at=None,
        metadata=None,
        created_by_account_id=None,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )


class _FakeRepository(MCPRepository):
    def __init__(self) -> None:
        super().__init__(prisma_client=None)
        self.records: dict[str, MCPServerRecord] = {"mcp-1": _server()}

    async def record_health_check(self, server_id: str, *, status, error, latency_ms):  # noqa: ANN001, ANN201
        server = self.records.get(server_id)
        if server is None:
            return None
        updated = MCPServerRecord(
            **{
                **server.__dict__,
                "last_health_status": status,
                "last_health_error": error,
                "last_health_latency_ms": latency_ms,
                "last_health_at": _utcnow(),
                "updated_at": _utcnow(),
            }
        )
        self.records[server_id] = updated
        return updated


class _HealthyTransport:
    async def initialize(self, server):  # noqa: ANN001
        return {"serverInfo": {"name": server.server_key}}

    async def list_tools(self, server):  # noqa: ANN001
        return []


class _FailingTransport:
    async def initialize(self, server):  # noqa: ANN001
        raise RuntimeError("upstream unavailable")

    async def list_tools(self, server):  # pragma: no cover
        return []


@pytest.mark.asyncio
async def test_health_probe_records_healthy_status() -> None:
    repository = _FakeRepository()
    registry = MCPRegistryService(repository)
    probe = MCPHealthProbe(registry, _HealthyTransport())  # type: ignore[arg-type]

    result = await probe.check_server(repository.records["mcp-1"])

    assert result.status == "healthy"
    assert repository.records["mcp-1"].last_health_status == "healthy"


@pytest.mark.asyncio
async def test_health_probe_records_unhealthy_status_on_failure() -> None:
    repository = _FakeRepository()
    registry = MCPRegistryService(repository)
    probe = MCPHealthProbe(registry, _FailingTransport())  # type: ignore[arg-type]

    result = await probe.check_server(repository.records["mcp-1"])

    assert result.status == "unhealthy"
    assert result.error == "upstream unavailable"
    assert repository.records["mcp-1"].last_health_status == "unhealthy"
