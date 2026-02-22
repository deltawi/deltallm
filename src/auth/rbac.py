from __future__ import annotations

from fastapi import HTTPException, Request, status


def require_role(*allowed_roles: str):
    role_set = {role.strip() for role in allowed_roles if role.strip()}

    async def _require(request: Request) -> None:
        auth_ctx = getattr(request.state, "user_api_key", None)
        role = getattr(auth_ctx, "user_role", None)
        if role not in role_set:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    return _require
