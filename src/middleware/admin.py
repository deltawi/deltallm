from __future__ import annotations

from fastapi import Header, HTTPException, Request, status

from src.middleware.platform_auth import has_platform_admin_session


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    if not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    return token or None


async def require_master_key(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> str:
    if has_platform_admin_session(request):
        return "platform_session"

    configured = None
    app_config = getattr(request.app.state, "app_config", None)
    if app_config is not None:
        configured = getattr(getattr(app_config, "general_settings", None), "master_key", None)
    if not configured:
        configured = getattr(getattr(request.app.state, "settings", None), "master_key", None)

    if not configured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Master key not configured")

    provided = x_master_key or _extract_bearer_token(authorization)
    if provided != configured:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid master key")

    return configured
