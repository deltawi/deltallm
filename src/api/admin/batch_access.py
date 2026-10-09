from __future__ import annotations

from fastapi import HTTPException, Request

from src.middleware.platform_auth import get_platform_auth_context


def require_operator_batch_access(request: Request) -> None:
    context = get_platform_auth_context(request)
    if context is not None and context.external_workspace is not None:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
