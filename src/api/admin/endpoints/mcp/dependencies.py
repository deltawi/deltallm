"""Request -> app.state accessors for the admin MCP endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status

from src.db.mcp import MCPRepository
from src.db.mcp_scope_policies import MCPScopePolicyRepository
from src.mcp.health import MCPHealthProbe
from src.mcp.registry import MCPRegistryService
from src.mcp.transport_http import StreamableHTTPMCPClient


def _repository_or_503(request: Request) -> MCPRepository:
    repository = getattr(request.app.state, "mcp_repository", None)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="MCP repository unavailable")
    return repository


def _registry_or_503(request: Request) -> MCPRegistryService:
    service = getattr(request.app.state, "mcp_registry_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="MCP registry service unavailable")
    return service


def _scope_policy_repository_or_503(request: Request) -> MCPScopePolicyRepository:
    repository = getattr(request.app.state, "mcp_scope_policy_repository", None)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="MCP scope policy repository unavailable")
    return repository


async def _reload_runtime_governance(request: Request, *, invalidate_registry: bool = True) -> None:
    invalidation = getattr(request.app.state, "governance_invalidation_service", None)
    if invalidation is not None and callable(getattr(invalidation, "invalidate_local", None)):
        await invalidation.invalidate_local("mcp")
        if callable(getattr(invalidation, "notify", None)):
            await invalidation.notify("mcp")
        return
    registry = getattr(request.app.state, "mcp_registry_service", None)
    if invalidate_registry and registry is not None and callable(getattr(registry, "invalidate_all", None)):
        await registry.invalidate_all()
    governance = getattr(request.app.state, "mcp_governance_service", None)
    if governance is not None and callable(getattr(governance, "reload", None)):
        await governance.reload()


def _transport_or_503(request: Request) -> StreamableHTTPMCPClient:
    client = getattr(request.app.state, "mcp_transport_client", None)
    if client is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="MCP transport client unavailable")
    return client


def _health_probe_or_503(request: Request) -> MCPHealthProbe:
    probe = getattr(request.app.state, "mcp_health_probe", None)
    if probe is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="MCP health probe unavailable")
    return probe


def _db_or_503(request: Request) -> Any:
    client = getattr(getattr(request.app.state, "prisma_manager", None), "client", None)
    if client is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")
    return client


__all__ = [
    "_repository_or_503",
    "_registry_or_503",
    "_scope_policy_repository_or_503",
    "_reload_runtime_governance",
    "_transport_or_503",
    "_health_probe_or_503",
    "_db_or_503",
]
