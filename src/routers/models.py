from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request

from src.middleware.auth import require_api_key
from src.router.runtime_generation import pin_routing_runtime_generation
from src.services.model_visibility import (
    filter_visible_models,
    get_callable_target_policy_mode_from_app,
    get_tier_policy_missing_service_mode_from_app,
    get_tier_policy_mode_from_app,
)

router = APIRouter(prefix="/v1", tags=["models"])


@router.get("/models", dependencies=[Depends(require_api_key)])
async def models(request: Request) -> dict[str, object]:
    now = int(time.time())
    auth = request.state.user_api_key
    routing_runtime = pin_routing_runtime_generation(request.app.state, request.state)
    callable_target_catalog = routing_runtime.callable_target_catalog
    callable_ids = list(callable_target_catalog.keys())
    model_ids = sorted(
        filter_visible_models(
            callable_ids,
            auth,
            callable_target_grant_service=getattr(
                request.app.state, "callable_target_grant_service", None
            ),
            callable_target_grant_snapshot=routing_runtime.authorization_snapshot,
            tier_policy_service=getattr(request.app.state, "tier_policy_service", None),
            policy_mode=get_callable_target_policy_mode_from_app(request.app),
            tier_policy_mode=get_tier_policy_mode_from_app(request.app),
            tier_policy_missing_service_mode=get_tier_policy_missing_service_mode_from_app(
                request.app
            ),
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
