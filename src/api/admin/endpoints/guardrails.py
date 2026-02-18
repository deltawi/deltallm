from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.api.admin.endpoints.common import serialize_guardrail
from src.config import GuardrailConfig
from src.middleware.admin import require_master_key

router = APIRouter(tags=["Admin Guardrails"])


@router.get("/ui/api/guardrails", dependencies=[Depends(require_master_key)])
async def get_guardrails(request: Request) -> dict[str, Any]:
    app_config = getattr(request.app.state, "app_config", None)
    if app_config is None:
        return {"guardrails": []}

    items = [serialize_guardrail(guardrail) for guardrail in app_config.litellm_settings.guardrails]
    return {"guardrails": items}


@router.put("/ui/api/guardrails", dependencies=[Depends(require_master_key)])
async def update_guardrails(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    app_config = getattr(request.app.state, "app_config", None)
    if app_config is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="App config unavailable")

    raw_guardrails = payload.get("guardrails")
    if not isinstance(raw_guardrails, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="guardrails must be an array")

    updated: list[GuardrailConfig] = []
    for raw in raw_guardrails:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("guardrail_name") or "").strip()
        litellm_params = raw.get("litellm_params")
        if not name or not isinstance(litellm_params, dict):
            continue
        updated.append(GuardrailConfig(guardrail_name=name, litellm_params=litellm_params))

    app_config.litellm_settings.guardrails = updated
    registry = getattr(request.app.state, "guardrail_registry", None)
    if registry is not None:
        if hasattr(registry, "_guardrails"):
            registry._guardrails.clear()
        if hasattr(registry, "_by_mode"):
            for mode in list(registry._by_mode):
                registry._by_mode[mode] = []
        registry.load_from_config(updated)

    return await get_guardrails(request)
