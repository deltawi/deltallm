from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.billing.cost import compute_billing_result
from src.billing.tier_pricing import attach_pricing_metadata, resolve_deployment_tier_pricing
from src.callbacks import CallbackManager, build_standard_logging_payload
from src.router.runtime_generation import pin_routing_runtime_generation
from src.middleware.auth import require_api_key
from src.middleware.rate_limit import (
    _release_rate_limits,
    acquire_parallel_limits_for_payload,
    check_and_acquire_rate_limits_for_payload,
)
from src.metrics import (
    increment_request,
    increment_request_failure,
    increment_spend,
    observe_api_latency,
    observe_request_latency,
)
from src.models.errors import InvalidRequestError
from src.models.requests import ImageGenerationRequest
from src.providers.base import parse_provider_json_response, validate_provider_success_payload
from src.providers.resolution import (
    normalize_openai_image_generation_payload,
    resolve_provider,
    resolve_upstream_model,
)
from src.router import ROUTING_MODE_CONTEXT_KEY
from src.upstream_auth import build_openai_compatible_auth_headers
from src.router.router import Deployment
from src.router.usage import record_router_usage
from src.telemetry.request_failures import enqueue_request_log_write, seed_request_failure_context
from src.telemetry.event_identity import get_or_create_billing_event_id
from src.upstream_http import build_upstream_request_timeout_for_request, configured_timeout_seconds
from src.audit.actions import AuditAction
from src.routers.audit_helpers import emit_audit_event
from src.routers.routing_decision import (
    attach_route_decision,
    capture_attempted_deployment,
    capture_initial_route_decision,
    route_failover_kwargs,
    route_decision_headers,
    resolve_failure_target,
    update_served_route_decision,
)
from src.routers.utils import enforce_budget_if_configured
from src.services.model_visibility import (
    ensure_model_allowed,
    get_callable_target_policy_mode_from_app,
    get_tier_policy_missing_service_mode_from_app,
    get_tier_policy_mode_from_app,
)
from src.services.preflight_capacity import acquire_preflight_capacity, release_preflight_capacity

router = APIRouter(prefix="/v1", tags=["images"])


def _is_valid_image_success_payload(data: Mapping[str, Any]) -> bool:
    images = data.get("data")
    if not isinstance(images, list) or not images:
        return False
    for item in images:
        if not isinstance(item, Mapping):
            return False
        url = item.get("url")
        encoded_image = item.get("b64_json")
        if not (
            (isinstance(url, str) and bool(url))
            or (isinstance(encoded_image, str) and bool(encoded_image))
        ):
            return False
    return True


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
    upstream_payload = payload.model_dump(exclude_none=True)
    provider = resolve_provider(params)
    headers = build_openai_compatible_auth_headers(
        provider=provider,
        api_key=str(api_key),
        auth_header_name=params.get("auth_header_name"),
        auth_header_format=params.get("auth_header_format"),
        content_type="application/json",
    )
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
        timeout=build_upstream_request_timeout_for_request(
            request, configured_timeout_seconds(params.get("timeout"))
        ),
    )
    if response.status_code >= 400:
        status_error = httpx.HTTPStatusError(
            f"Upstream image generation failed with status {response.status_code}",
            request=httpx.Request("POST", f"{api_base}/images/generations"),
            response=response,
        )
        raise request.app.state.provider_error_mapper_registry.map_error(provider, status_error)
    data = parse_provider_json_response(response)
    validate_provider_success_payload(data, _is_valid_image_success_payload)
    data["_api_latency_ms"] = (perf_counter() - upstream_start) * 1000
    data["_api_base"] = api_base
    data["_deployment_model"] = params.get("model")
    data["_model_info"] = deployment.model_info
    return data


@router.post("/images/generations", dependencies=[Depends(require_api_key)])
async def image_generations(request: Request, payload: ImageGenerationRequest):
    request_start = perf_counter()
    callback_start = datetime.now(tz=UTC)
    seed_request_failure_context(
        request,
        call_type="image_generation",
        model=payload.model,
        request_start=request_start,
        audit_action=AuditAction.IMAGE_GENERATION_REQUEST,
    )
    auth = request.state.user_api_key
    routing_runtime = pin_routing_runtime_generation(request.app.state, request.state)
    await acquire_preflight_capacity(request, auth=auth)
    ensure_model_allowed(
        auth,
        payload.model,
        callable_target_grant_service=getattr(
            request.app.state, "callable_target_grant_service", None
        ),
        callable_target_grant_snapshot=routing_runtime.authorization_snapshot,
        tier_policy_service=getattr(request.app.state, "tier_policy_service", None),
        policy_mode=get_callable_target_policy_mode_from_app(request.app),
        tier_policy_mode=get_tier_policy_mode_from_app(request.app),
        tier_policy_missing_service_mode=get_tier_policy_missing_service_mode_from_app(request.app),
        emit_shadow_log=True,
    )
    request_data = payload.model_dump(exclude_none=True)
    await acquire_parallel_limits_for_payload(request, model=payload.model, payload=request_data)
    try:
        await enforce_budget_if_configured(request, model=payload.model, auth=auth)
    except Exception:
        await _release_rate_limits(request)
        await release_preflight_capacity(request)
        raise

    callback_manager: CallbackManager = getattr(
        request.app.state, "callback_manager", CallbackManager()
    )
    try:
        await check_and_acquire_rate_limits_for_payload(
            request,
            model=payload.model,
            payload=request_data,
        )
    finally:
        await release_preflight_capacity(request)

    app_router = routing_runtime.router
    model_group = app_router.resolve_model_group(payload.model)
    request_context = {
        "metadata": {},
        "user_id": auth.user_id or auth.api_key,
        ROUTING_MODE_CONTEXT_KEY: "image_generation",
    }
    primary = app_router.require_deployment(
        model_group=model_group,
        deployment=await app_router.select_deployment(model_group, request_context),
    )
    failover_kwargs = route_failover_kwargs(request_context)
    capture_initial_route_decision(request, request_context)
    api_provider = resolve_provider(primary.deltallm_params)
    request_id = request.headers.get("x-request-id")
    primary_api_base = str(
        primary.deltallm_params.get("api_base", request.app.state.settings.openai_base_url)
    ).rstrip("/")

    def track_attempt(deployment):  # noqa: ANN001
        capture_attempted_deployment(request, deployment)

    try:
        data, served_deployment = await routing_runtime.failover_manager.execute_with_failover(
            primary_deployment=primary,
            model_group=model_group,
            execute=lambda dep: _execute_image_generation(request, payload, dep),
            return_deployment=True,
            on_attempt=track_attempt,
            routing_context=request_context,
            **failover_kwargs,
        )
        update_served_route_decision(
            request,
            primary_deployment_id=primary.deployment_id,
            served_deployment_id=served_deployment.deployment_id,
        )
        api_provider = resolve_provider(served_deployment.deltallm_params)

        api_latency_ms = data.pop("_api_latency_ms", 0)
        api_base = data.pop("_api_base", "")
        deployment_model = data.pop("_deployment_model", None)
        data.pop("_model_info", None)

        num_images = len(data.get("data", []))
        # `images` remains for router/spend compatibility; generated images are outputs.
        usage = {"images": num_images, "output_images": num_images}
        await record_router_usage(
            request.app.state.router_state_backend,
            served_deployment.deployment_id,
            mode="image_generation",
            usage=usage,
        )
        pricing = resolve_deployment_tier_pricing(
            auth=auth,
            model=payload.model,
            deployment=served_deployment,
            tier_policy_service=getattr(request.app.state, "tier_policy_service", None),
            mode="sync",
        )
        billing = compute_billing_result(
            mode="image_generation",
            usage=usage,
            model_info=pricing.customer_model_info,
        )
        provider_billing = compute_billing_result(
            mode="image_generation",
            usage=usage,
            model_info=pricing.provider_model_info,
        )
        request_cost = billing.cost
        provider_cost = (
            None if provider_billing.unpriced_reason is not None else provider_billing.cost
        )
        increment_request(
            model=payload.model,
            api_provider=api_provider,
            api_key=auth.api_key,
            user=auth.user_id,
            team=auth.team_id,
            status_code=200,
        )
        increment_spend(
            model=payload.model,
            api_provider=api_provider,
            api_key=auth.api_key,
            user=auth.user_id,
            team=auth.team_id,
            spend=request_cost,
        )
        await enqueue_request_log_write(
            request,
            request.app.state.spend_tracking_service.log_spend(
                event_id=get_or_create_billing_event_id(request),
                request_id=request_id or "",
                api_key=auth.api_key,
                user_id=auth.user_id,
                team_id=auth.team_id,
                organization_id=getattr(auth, "organization_id", None),
                owner_account_id=getattr(auth, "owner_account_id", None),
                end_user_id=None,
                model=payload.model,
                call_type="image_generation",
                usage=usage,
                cost=request_cost,
                metadata=attach_route_decision(
                    attach_pricing_metadata(
                        {
                            "api_base": api_base,
                            "provider": api_provider,
                            "deployment_model": deployment_model,
                        },
                        pricing,
                        provider_cost=provider_cost,
                        billing=billing,
                        provider_billing=provider_billing,
                    ),
                    request,
                ),
                cache_hit=False,
                start_time=callback_start,
                end_time=datetime.now(tz=UTC),
            ),
        )
        observe_request_latency(
            model=payload.model,
            api_provider=api_provider,
            status_code=200,
            latency_seconds=perf_counter() - request_start,
        )
        observe_api_latency(
            model=payload.model, api_provider=api_provider, latency_seconds=api_latency_ms / 1000
        )
        callback_payload = build_standard_logging_payload(
            call_type="image_generation",
            request_id=request_id,
            model=payload.model,
            deployment_model=deployment_model,
            request_payload=request_data,
            response_obj=data,
            user_api_key_dict=auth.model_dump(mode="json"),
            start_time=callback_start,
            end_time=datetime.now(tz=UTC),
            api_base=api_base,
            response_cost=request_cost,
            api_latency_ms=api_latency_ms,
            api_provider=api_provider,
            turn_off_message_logging=bool(
                getattr(request.app.state, "turn_off_message_logging", False)
            ),
        )
        callback_manager.dispatch_success_callbacks(callback_payload)
        await emit_audit_event(
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
        failure_target = resolve_failure_target(request, fallback_deployment=primary)
        failure_provider = str(failure_target.provider or api_provider)
        failure_api_base = failure_target.api_base or primary_api_base
        failure_deployment_model = failure_target.deployment_model or primary.deltallm_params.get(
            "model"
        )
        status_code = getattr(getattr(exc, "response", None), "status_code", 502)
        increment_request(
            model=payload.model,
            api_provider=failure_provider,
            api_key=auth.api_key,
            user=auth.user_id,
            team=auth.team_id,
            status_code=status_code,
        )
        increment_request_failure(
            model=payload.model, api_provider=failure_provider, error_type=exc.__class__.__name__
        )
        observe_request_latency(
            model=payload.model,
            api_provider=failure_provider,
            status_code=status_code,
            latency_seconds=perf_counter() - request_start,
        )
        await enqueue_request_log_write(
            request,
            request.app.state.spend_tracking_service.log_request_failure(
                event_id=get_or_create_billing_event_id(request),
                request_id=request_id or "",
                api_key=auth.api_key,
                user_id=auth.user_id,
                team_id=auth.team_id,
                organization_id=getattr(auth, "organization_id", None),
                owner_account_id=getattr(auth, "owner_account_id", None),
                end_user_id=None,
                model=payload.model,
                call_type="image_generation",
                metadata=attach_route_decision(
                    {
                        "route": request.url.path,
                        "provider": failure_provider,
                        "api_base": failure_api_base,
                        "deployment_model": failure_deployment_model,
                    },
                    request,
                ),
                cache_hit=False,
                start_time=callback_start,
                end_time=datetime.now(tz=UTC),
                http_status_code=status_code,
                exc=exc,
            ),
        )
        await emit_audit_event(
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
            metadata=attach_route_decision(
                {
                    "route": request.url.path,
                    "provider": failure_provider,
                    "api_base": failure_api_base,
                    "deployment_model": failure_deployment_model,
                },
                request,
            ),
        )
        raise InvalidRequestError(message=f"Image generation request failed: {exc}") from exc
    except Exception as exc:
        failure_target = resolve_failure_target(request, fallback_deployment=primary)
        failure_provider = str(failure_target.provider or api_provider)
        failure_api_base = failure_target.api_base or primary_api_base
        failure_deployment_model = failure_target.deployment_model or primary.deltallm_params.get(
            "model"
        )
        status_code = int(getattr(exc, "status_code", 500) or 500)
        await enqueue_request_log_write(
            request,
            request.app.state.spend_tracking_service.log_request_failure(
                event_id=get_or_create_billing_event_id(request),
                request_id=request_id or "",
                api_key=auth.api_key,
                user_id=auth.user_id,
                team_id=auth.team_id,
                organization_id=getattr(auth, "organization_id", None),
                owner_account_id=getattr(auth, "owner_account_id", None),
                end_user_id=None,
                model=payload.model,
                call_type="image_generation",
                metadata=attach_route_decision(
                    {
                        "route": request.url.path,
                        "provider": failure_provider,
                        "api_base": failure_api_base,
                        "deployment_model": failure_deployment_model,
                    },
                    request,
                ),
                cache_hit=False,
                start_time=callback_start,
                end_time=datetime.now(tz=UTC),
                http_status_code=status_code,
                exc=exc,
            ),
        )
        await emit_audit_event(
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
            metadata=attach_route_decision(
                {
                    "route": request.url.path,
                    "provider": failure_provider,
                    "api_base": failure_api_base,
                    "deployment_model": failure_deployment_model,
                },
                request,
            ),
        )
        raise
