from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query, Request

from src.middleware.auth import require_api_key
from src.models.errors import ModelNotFoundError
from src.router.runtime_generation import RoutingRuntimeGeneration, pin_routing_runtime_generation
from src.services.access.model_visibility import (
    filter_visible_models,
    get_callable_target_policy_mode_from_app,
    get_tier_policy_missing_service_mode_from_app,
    get_tier_policy_mode_from_app,
)
from src.services.models.public_model_info import deployment_info, model_group_info

# Paths are spelled out because the LiteLLM-compatible info routes live outside /v1.
router = APIRouter(tags=["models"])


def _visible_model_ids(
    request: Request,
    routing_runtime: RoutingRuntimeGeneration,
    *,
    emit_shadow_log: bool = False,
) -> list[str]:
    return sorted(
        filter_visible_models(
            list(routing_runtime.callable_target_catalog.keys()),
            request.state.user_api_key,
            callable_target_grant_service=getattr(
                request.app.state, "callable_target_grant_service", None
            ),
            callable_target_grant_snapshot=routing_runtime.authorization_snapshot,
            creator_model_access_snapshot=routing_runtime.creator_model_access_snapshot,
            creator_route_group_access_snapshot=(
                routing_runtime.creator_route_group_access_snapshot
            ),
            tier_policy_service=getattr(request.app.state, "tier_policy_service", None),
            policy_mode=get_callable_target_policy_mode_from_app(request.app),
            tier_policy_mode=get_tier_policy_mode_from_app(request.app),
            tier_policy_missing_service_mode=get_tier_policy_missing_service_mode_from_app(
                request.app
            ),
            emit_shadow_log=emit_shadow_log,
        )
    )


def _model_object(model_id: str, created: int) -> dict[str, object]:
    return {"id": model_id, "object": "model", "created": created, "owned_by": "deltallm"}


@router.get("/v1/models", dependencies=[Depends(require_api_key)])
async def models(request: Request) -> dict[str, object]:
    now = int(time.time())
    routing_runtime = pin_routing_runtime_generation(request.app.state, request.state)
    model_ids = _visible_model_ids(request, routing_runtime, emit_shadow_log=True)
    return {"object": "list", "data": [_model_object(model_id, now) for model_id in model_ids]}


@router.get("/v1/models/{model_id:path}", dependencies=[Depends(require_api_key)])
async def retrieve_model(request: Request, model_id: str) -> dict[str, object]:
    routing_runtime = pin_routing_runtime_generation(request.app.state, request.state)
    if model_id not in _visible_model_ids(request, routing_runtime):
        raise ModelNotFoundError(f"The model '{model_id}' does not exist", param="model")
    return _model_object(model_id, int(time.time()))


@router.get("/model/info", dependencies=[Depends(require_api_key)])
@router.get("/v1/model/info", dependencies=[Depends(require_api_key)])
async def model_info(
    request: Request,
    litellm_model_id: str | None = Query(
        default=None, description="Return only the deployment with this id."
    ),
) -> dict[str, object]:
    """Per-deployment metadata for every model the caller can call, LiteLLM-compatible.

    Provider keys, endpoints and credentials are never included.
    """
    routing_runtime = pin_routing_runtime_generation(request.app.state, request.state)
    catalog = routing_runtime.callable_target_catalog
    groups = routing_runtime.deployment_registry.snapshot()
    data: list[dict[str, object]] = []
    for model_id in _visible_model_ids(request, routing_runtime):
        target = catalog.get(model_id)
        mode = getattr(target, "mode", None)
        for deployment in groups.get(model_id, ()):
            if litellm_model_id is not None and deployment.deployment_id != litellm_model_id:
                continue
            data.append(deployment_info(model_id, deployment, mode=mode))
    if litellm_model_id is not None and not data:
        raise ModelNotFoundError(
            f"Model id '{litellm_model_id}' was not found", param="litellm_model_id"
        )
    return {"data": data}


@router.get("/model_group/info", dependencies=[Depends(require_api_key)])
async def model_group_info_endpoint(
    request: Request,
    model_group: str | None = Query(
        default=None, description="Return only this public model name."
    ),
) -> dict[str, object]:
    """One aggregated entry per public model name the caller can call, LiteLLM-compatible."""
    routing_runtime = pin_routing_runtime_generation(request.app.state, request.state)
    catalog = routing_runtime.callable_target_catalog
    groups = routing_runtime.deployment_registry.snapshot()
    data: list[dict[str, object]] = []
    for model_id in _visible_model_ids(request, routing_runtime):
        if model_group is not None and model_id != model_group:
            continue
        target = catalog.get(model_id)
        data.append(
            model_group_info(
                model_id,
                groups.get(model_id, ()),
                mode=getattr(target, "mode", None),
                target_type=getattr(target, "target_type", "model"),
            )
        )
    return {"data": data}
