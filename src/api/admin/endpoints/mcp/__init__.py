"""Admin MCP endpoints package.

Keeps the public import surface stable: callers continue to do

    from src.api.admin.endpoints.mcp import router

and receive the aggregate :class:`APIRouter` composed of the per-concern
sub-routers defined under :mod:`src.api.admin.endpoints.mcp.routes`.
"""
from __future__ import annotations

from src.api.admin.endpoints.mcp.router import router

__all__ = ["router"]
