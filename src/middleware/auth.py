from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status

from src.models.errors import AuthenticationError
from src.models.responses import UserAPIKeyAuth
from src.services.key_service import KeyService


async def require_api_key(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    raw_key = authorization.split(" ", 1)[1].strip()
    if not raw_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing API key")

    if _is_master_key(request, raw_key):
        auth = UserAPIKeyAuth(
            api_key="master_key",
            user_id="admin",
            user_role="platform_admin",
            metadata={"is_master_key": True},
        )
        request.state.user_api_key = auth
        request.state.auth_context = auth
        return

    key_service: KeyService | None = getattr(request.app.state, "key_service", None)
    if key_service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Key service not configured")

    try:
        auth = await key_service.validate_key(raw_key)
    except AuthenticationError as exc:
        auth = await _try_fallback_auth(request, raw_key)
        if auth is None:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    request.state.user_api_key = auth
    request.state.auth_context = auth


def auth_dependency() -> Depends:
    return Depends(require_api_key)


def _is_master_key(request: Request, token: str) -> bool:
    dcm = getattr(request.app.state, "dynamic_config_manager", None)
    if dcm is not None:
        cfg = dcm.get_app_config()
        configured = getattr(getattr(cfg, "general_settings", None), "master_key", None)
    else:
        configured = getattr(getattr(request.app.state, "settings", None), "master_key", None)
    return bool(configured and token == configured)


async def _try_fallback_auth(request: Request, raw_token: str) -> UserAPIKeyAuth | None:
    jwt_handler = getattr(request.app.state, "jwt_auth_handler", None)
    if jwt_handler is not None:
        try:
            claims = await jwt_handler.validate_token(raw_token)
            return UserAPIKeyAuth(
                api_key=f"jwt:{claims.get('user_id') or claims.get('email') or 'unknown'}",
                user_id=claims.get("user_id"),
                team_id=claims.get("team_id"),
                organization_id=claims.get("organization_id"),
                user_role=claims.get("user_role"),
                metadata={"jwt_claims": claims.get("claims", claims)},
            )
        except HTTPException:
            pass

    custom_auth_manager = getattr(request.app.state, "custom_auth_manager", None)
    if custom_auth_manager is not None:
        return await custom_auth_manager.authenticate(raw_token, request)

    return None
