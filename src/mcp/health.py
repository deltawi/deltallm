from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from src.db.mcp import MCPServerRecord

from .metrics import record_mcp_health_check
from .registry import MCPRegistryService, server_record_to_config
from .transport_http import StreamableHTTPMCPClient


@dataclass(frozen=True)
class MCPHealthResult:
    status: str
    latency_ms: int
    error: str | None = None


class MCPHealthProbe:
    def __init__(self, registry: MCPRegistryService, client: StreamableHTTPMCPClient) -> None:
        self.registry = registry
        self.client = client

    async def check_server(self, server: MCPServerRecord) -> MCPHealthResult:
        started = perf_counter()
        try:
            config = server_record_to_config(server)
            await self.client.initialize(config)
            await self.client.list_tools(config)
        except Exception as exc:
            latency_ms = int((perf_counter() - started) * 1000)
            await self.registry.record_health(
                server.mcp_server_id,
                status="unhealthy",
                error=str(exc),
                latency_ms=latency_ms,
            )
            record_mcp_health_check(server_key=server.server_key, status="unhealthy", latency_ms=latency_ms)
            return MCPHealthResult(status="unhealthy", latency_ms=latency_ms, error=str(exc))

        latency_ms = int((perf_counter() - started) * 1000)
        await self.registry.record_health(
            server.mcp_server_id,
            status="healthy",
            error=None,
            latency_ms=latency_ms,
        )
        record_mcp_health_check(server_key=server.server_key, status="healthy", latency_ms=latency_ms)
        return MCPHealthResult(status="healthy", latency_ms=latency_ms)
