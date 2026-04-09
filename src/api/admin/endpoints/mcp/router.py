"""Top-level aggregate router for the admin MCP endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from src.api.admin.endpoints.mcp.routes import (
    approvals,
    bindings,
    migration,
    scope_policies,
    servers,
    tool_policies,
)

router = APIRouter()

# Preserve the original route-registration order so OpenAPI output and any
# first-match dispatch behavior remain identical to the pre-split module.
router.include_router(servers.router)
router.include_router(bindings.router)
router.include_router(scope_policies.router)
router.include_router(tool_policies.router)
router.include_router(approvals.router)
router.include_router(migration.router)

__all__ = ["router"]
