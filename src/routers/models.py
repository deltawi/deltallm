from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request

from src.middleware.auth import require_api_key
from src.services.callable_targets import list_callable_target_ids
from src.services.model_visibility import filter_visible_models, get_callable_target_policy_mode_from_app

router = APIRouter(prefix="/v1", tags=["models"])


@router.get("/models", dependencies=[Depends(require_api_key)])
async def models(request: Request) -> dict[str, object]:
    now = int(time.time())
    auth = request.state.user_api_key
    callable_target_catalog = getattr(request.app.state, "callable_target_catalog", None)
    if isinstance(callable_target_catalog, dict):
        callable_ids = list(callable_target_catalog.keys())
    else:
        callable_ids = list_callable_target_ids(
            getattr(request.app.state, "model_registry", {}) or {},
            getattr(request.app.state, "route_groups", []) or [],
        )
    model_ids = sorted(
        filter_visible_models(
            callable_ids,
            auth,
            callable_target_grant_service=getattr(request.app.state, "callable_target_grant_service", None),
            policy_mode=get_callable_target_policy_mode_from_app(request.app),
            emit_shadow_log=True,
        )
    )
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "created": now,
                "owned_by": "deltallm",
            }
            for model_id in model_ids
        ],
    }
