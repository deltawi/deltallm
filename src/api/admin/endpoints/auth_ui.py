from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["Auth"])


@router.get("/ui/api/auth/sso-url")
async def get_sso_url() -> dict[str, str]:
    return {"url": "/auth/login"}
