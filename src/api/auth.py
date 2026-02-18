from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def auth_login(request: Request, state: str = Query(default="")):
    handler = getattr(request.app.state, "sso_auth_handler", None)
    if handler is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO is not enabled")

    if not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing state")

    return {"authorize_url": handler.get_authorize_url(state)}


@router.get("/callback")
async def auth_callback(request: Request, code: str = Query(default=""), state: str | None = Query(default=None)):
    handler = getattr(request.app.state, "sso_auth_handler", None)
    if handler is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SSO is not enabled")

    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing code")

    response = await handler.handle_callback(code)
    if state is not None:
        response["state"] = state
    return response
