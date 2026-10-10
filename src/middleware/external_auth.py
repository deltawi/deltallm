from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from src.auth.external_policy import EXTERNAL_SESSION_PREFIX
from src.services.identity.master_session_service import MASTER_SESSION_COOKIE_NAME


def carries_external_session(request: Request) -> bool:
    return (
        getattr(request, "cookies", {})
        .get("deltallm_session", "")
        .startswith(EXTERNAL_SESSION_PREFIX)
    )


def require_unmixed_external_auth(
    request: Request, *, authorization: str | None = None, x_master_key: str | None = None
) -> None:
    context = getattr(getattr(request, "state", None), "platform_auth", None)
    if (
        not carries_external_session(request)
        and getattr(context, "external_workspace", None) is None
    ):
        return
    from src.middleware.platform_auth import get_configured_master_key

    authorization = authorization or request.headers.get("authorization")
    x_master_key = x_master_key or request.headers.get("x-master-key")
    configured = get_configured_master_key(request)
    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization.split(" ", 1)[1].strip()
    if (
        request.cookies.get(MASTER_SESSION_COOKIE_NAME)
        or x_master_key
        or (configured and bearer and hmac.compare_digest(bearer, configured))
    ):
        raise HTTPException(
            status_code=403,
            detail="Mixed authentication is denied",
            headers={"Cache-Control": "no-store"},
        )


def external_session_unavailable(request: Request) -> bool:
    return getattr(getattr(request, "state", None), "external_session_unavailable", False) is True


def require_external_browser_request(request: Request) -> None:
    if not carries_external_session(request) or request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    runtime = getattr(request.app.state, "external_auth_runtime", None)
    resolver = getattr(runtime, "client_resolver", None)
    if resolver is None or not resolver.accepts_origin(request.headers.get("origin")):
        raise HTTPException(
            status_code=403,
            detail="External request origin is denied",
            headers={"Cache-Control": "no-store"},
        )
