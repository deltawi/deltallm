from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.billing.cost import compute_cost
from src.callbacks import CallbackManager, build_standard_logging_payload
from src.middleware.auth import require_api_key
from src.middleware.rate_limit import enforce_rate_limits
from src.metrics import (
    increment_request,
    increment_request_failure,
    increment_spend,
    observe_api_latency,
    observe_request_latency,
)
from src.models.errors import InvalidRequestError
from src.models.requests import ImageGenerationRequest
from src.providers.resolution import (
    normalize_openai_image_generation_payload,
    resolve_provider,
    resolve_upstream_model,
)
from src.router.router import Deployment
from src.audit.actions import AuditAction
from src.routers.audit_helpers import emit_audit_event
from src.routers.routing_decision import (
    attach_route_decision,
    capture_initial_route_decision,
    route_failover_kwargs,
    route_decision_headers,
    update_served_route_decision,
)
from src.routers.utils import enforce_budget_if_configured, fire_and_forget
from src.services.model_visibility import ensure_model_allowed, get_callable_target_policy_mode_from_app

router = APIRouter(prefix="/v1", tags=["images"])


async def _execute_image_generation(
    request: Request,
    payload: ImageGenerationRequest,
    deployment: Deployment,
) -> dict[str, Any]:
    params = deployment.deltallm_params
    api_key = params.get("api_key")
    if not api_key:
        raise InvalidRequestError(message="Provider API key is missing for selected model")

    api_base = params.get("api_base", request.app.state.settings.openai_base_url).rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    upstream_payload = payload.model_dump(exclude_none=True)
    provider = resolve_provider(params)
    upstream_model = resolve_upstream_model(params)
    if upstream_model:
        upstream_payload["model"] = upstream_model
    normalize_openai_image_generation_payload(
        upstream_payload,
        provider=provider,
        upstream_model=upstream_model or str(upstream_payload.get("model") or ""),
    )

    from src.routers.utils import apply_default_params
    apply_default_params(upstream_payload, deployment.model_info)

    upstream_start = perf_counter()
    response = await request.app.state.http_client.post(
        f"{api_base}/images/generations",
        headers=headers,
        json=upstream_payload,
        timeout=params.get("timeout") or 300,
    )
    if response.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"Upstream image generation failed with status {response.status_code}",
            request=httpx.Request("POST", f"{api_base}/images/generations"),
            response=response,
        )
    data = response.json()
    data["_api_latency_ms"] = (perf_counter() - upstream_start) * 1000
    data["_api_base"] = api_base
    data["_deployment_model"] = params.get("model")
    data["_model_info"] = deployment.model_info
    return data


@router.post("/images/generations", dependencies=[Depends(require_api_key), Depends(enforce_rate_limits)])
async def image_generations(request: Request, payload: ImageGenerationRequest):
    request_start = perf_counter()
    callback_start = datetime.now(tz=UTC)
    auth = request.state.user_api_key
    ensure_model_allowed(
        auth,
        payload.model,
        callable_target_grant_service=getattr(request.app.state, "callable_target_grant_service", None),
        policy_mode=get_callable_target_policy_mode_from_app(request.app),
        emit_shadow_log=True,
    )
    await enforce_budget_if_configured(request, model=payload.model, auth=auth)

    callback_manager: CallbackManager = getattr(request.app.state, "callback_manager", CallbackManager())
    request_data = payload.model_dump(exclude_none=True)

    app_router = request.app.state.router
    model_group = app_router.resolve_model_group(payload.model)
    request_context = {"metadata": {}, "user_id": auth.user_id or auth.api_key}
    primary = app_router.require_deployment(
        model_group=model_group,
        deployment=await app_router.select_deployment(model_group, request_context),
    )
    failover_kwargs = route_failover_kwargs(request_context)
    capture_initial_route_decision(request, request_context)
    api_provider = resolve_provider(primary.deltallm_params)
    request_id = request.headers.get("x-request-id")

    try:
        data, served_deployment = await request.app.state.failover_manager.execute_with_failover(
            primary_deployment=primary,
            model_group=model_group,
            execute=lambda dep: _execute_image_generation(request, payload, dep),
            return_deployment=True,
            **failover_kwargs,
        )
        update_served_route_decision(
            request,
            primary_deployment_id=primary.deployment_id,
            served_deployment_id=served_deployment.deployment_id,
        )
        await request.app.state.passive_health_tracker.record_request_outcome(served_deployment.deployment_id, success=True)
        api_provider = resolve_provider(served_deployment.deltallm_params)

        api_latency_ms = data.pop("_api_latency_ms", 0)
        api_base = data.pop("_api_base", "")
        deployment_model = data.pop("_deployment_model", None)
        model_info = data.pop("_model_info", {})

        num_images = len(data.get("data", []))
        usage = {"images": num_images}
        request_cost = compute_cost(mode="image_generation", usage=usage, model_info=model_info)
        increment_request(
            model=payload.model, api_provider=api_provider,
            api_key=auth.api_key, user=auth.user_id, team=auth.team_id, status_code=200,
        )
        increment_spend(
            model=payload.model, api_provider=api_provider,
            api_key=auth.api_key, user=auth.user_id, team=auth.team_id, spend=request_cost,
        )
        fire_and_forget(
            request.app.state.spend_tracking_service.log_spend(
                request_id=request_id or "",
                api_key=auth.api_key,
                user_id=auth.user_id,
                team_id=auth.team_id,
                organization_id=getattr(auth, "organization_id", None),
                end_user_id=None,
                model=payload.model,
                call_type="image_generation",
                usage=usage,
                cost=request_cost,
                metadata=attach_route_decision({"api_base": api_base}, request),
                cache_hit=False,
                start_time=callback_start,
                end_time=datetime.now(tz=UTC),
            )
        )
        observe_request_latency(model=payload.model, api_provider=api_provider, status_code=200, latency_seconds=perf_counter() - request_start)
        observe_api_latency(model=payload.model, api_provider=api_provider, latency_seconds=api_latency_ms / 1000)
        callback_payload = build_standard_logging_payload(
            call_type="image_generation", request_id=request_id, model=payload.model,
            deployment_model=deployment_model, request_payload=request_data, response_obj=data,
            user_api_key_dict=auth.model_dump(mode="json"), start_time=callback_start, end_time=datetime.now(tz=UTC),
            api_base=api_base, response_cost=request_cost, api_latency_ms=api_latency_ms,
            turn_off_message_logging=bool(getattr(request.app.state, "turn_off_message_logging", False)),
        )
        callback_manager.dispatch_success_callbacks(callback_payload)
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.IMAGE_GENERATION_REQUEST,
            status="success",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="model",
            resource_id=payload.model,
            request_payload=request_data,
            response_payload=data,
            metadata=attach_route_decision(
                {
                    "route": request.url.path,
                    "provider": api_provider,
                    "api_base": api_base,
                    "deployment_model": deployment_model,
                    "images": num_images,
                },
                request,
            ),
        )
        return JSONResponse(status_code=200, content=data, headers=route_decision_headers(request))
    except httpx.HTTPError as exc:
        await request.app.state.passive_health_tracker.record_request_outcome(primary.deployment_id, success=False, error=str(exc))
        status_code = getattr(getattr(exc, "response", None), "status_code", 502)
        increment_request(model=payload.model, api_provider=api_provider, api_key=auth.api_key, user=auth.user_id, team=auth.team_id, status_code=status_code)
        increment_request_failure(model=payload.model, api_provider=api_provider, error_type=exc.__class__.__name__)
        observe_request_latency(model=payload.model, api_provider=api_provider, status_code=status_code, latency_seconds=perf_counter() - request_start)
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.IMAGE_GENERATION_REQUEST,
            status="error",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="model",
            resource_id=payload.model,
            request_payload=request_data,
            error=exc,
            metadata=attach_route_decision({"route": request.url.path, "provider": api_provider}, request),
        )
        raise InvalidRequestError(message=f"Image generation request failed: {exc}") from exc
    except Exception as exc:
        await request.app.state.passive_health_tracker.record_request_outcome(primary.deployment_id, success=False, error=str(exc))
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.IMAGE_GENERATION_REQUEST,
            status="error",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="model",
            resource_id=payload.model,
            request_payload=request_data,
            error=exc,
            metadata=attach_route_decision({"route": request.url.path, "provider": api_provider}, request),
        )
        raise
