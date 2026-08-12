from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.billing.tier_pricing import (
    attach_pricing_metadata,
    resolve_deployment_tier_pricing,
    resolve_token_billing_result,
)
from src.callbacks import CallbackManager, build_standard_logging_payload
from src.middleware.auth import require_api_key
from src.middleware.rate_limit import check_and_acquire_rate_limits_for_payload
from src.metrics import (
    increment_request,
    increment_request_failure,
    increment_spend,
    observe_api_latency,
    observe_request_latency,
)
from src.models.errors import InvalidRequestError
from src.models.requests import RerankRequest
from src.providers.resolution import resolve_provider, resolve_upstream_model
from src.upstream_auth import build_openai_compatible_auth_headers
from src.upstream_http import build_upstream_request_timeout_for_request, configured_timeout_seconds
from src.router.router import Deployment
from src.router.usage import record_router_usage
from src.audit.actions import AuditAction
from src.telemetry.request_failures import enqueue_request_log_write, seed_request_failure_context
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
from src.routers.utils import enforce_budget_if_configured, fire_and_forget
from src.services.model_visibility import (
    ensure_model_allowed,
    get_callable_target_policy_mode_from_app,
    get_tier_policy_missing_service_mode_from_app,
    get_tier_policy_mode_from_app,
)

router = APIRouter(prefix="/v1", tags=["rerank"])


async def _execute_rerank(
    request: Request,
    payload: RerankRequest,
    deployment: Deployment,
) -> dict[str, Any]:
    params = deployment.deltallm_params
    api_key = params.get("api_key")
    if not api_key:
        raise InvalidRequestError(message="Provider API key is missing for selected model")

    api_base = params.get("api_base", request.app.state.settings.openai_base_url).rstrip("/")
    headers = build_openai_compatible_auth_headers(
        provider=resolve_provider(params),
        api_key=str(api_key),
        auth_header_name=params.get("auth_header_name"),
        auth_header_format=params.get("auth_header_format"),
        content_type="application/json",
    )

    upstream_payload = payload.model_dump(exclude_none=True)
    upstream_model = resolve_upstream_model(params)
    if upstream_model:
        upstream_payload["model"] = upstream_model

    from src.routers.utils import apply_default_params
    apply_default_params(upstream_payload, deployment.model_info)

    upstream_start = perf_counter()
    response = await request.app.state.http_client.post(
        f"{api_base}/rerank",
        headers=headers,
        json=upstream_payload,
        timeout=build_upstream_request_timeout_for_request(request, configured_timeout_seconds(params.get("timeout"))),
    )
    if response.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"Upstream rerank call failed with status {response.status_code}",
            request=httpx.Request("POST", f"{api_base}/rerank"),
            response=response,
        )
    data = response.json()
    data["_api_latency_ms"] = (perf_counter() - upstream_start) * 1000
    data["_api_base"] = api_base
    data["_deployment_model"] = params.get("model")
    data["_model_info"] = deployment.model_info
    return data


@router.post("/rerank", dependencies=[Depends(require_api_key)])
async def rerank(request: Request, payload: RerankRequest):
    request_start = perf_counter()
    callback_start = datetime.now(tz=UTC)
    seed_request_failure_context(
        request,
        call_type="rerank",
        model=payload.model,
        request_start=request_start,
        audit_action=AuditAction.RERANK_REQUEST,
    )
    auth = request.state.user_api_key
    ensure_model_allowed(
        auth,
        payload.model,
        callable_target_grant_service=getattr(request.app.state, "callable_target_grant_service", None),
        tier_policy_service=getattr(request.app.state, "tier_policy_service", None),
        policy_mode=get_callable_target_policy_mode_from_app(request.app),
        tier_policy_mode=get_tier_policy_mode_from_app(request.app),
        tier_policy_missing_service_mode=get_tier_policy_missing_service_mode_from_app(request.app),
        emit_shadow_log=True,
    )
    await enforce_budget_if_configured(request, model=payload.model, auth=auth)

    callback_manager: CallbackManager = getattr(request.app.state, "callback_manager", CallbackManager())
    request_data = payload.model_dump(exclude_none=True)
    await check_and_acquire_rate_limits_for_payload(
        request,
        model=payload.model,
        payload=request_data,
    )

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
    primary_api_base = str(primary.deltallm_params.get("api_base", request.app.state.settings.openai_base_url)).rstrip("/")

    def track_attempt(deployment):  # noqa: ANN001
        capture_attempted_deployment(request, deployment)

    try:
        data, served_deployment = await request.app.state.failover_manager.execute_with_failover(
            primary_deployment=primary,
            model_group=model_group,
            execute=lambda dep: _execute_rerank(request, payload, dep),
            return_deployment=True,
            on_attempt=track_attempt,
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
        data.pop("_model_info", None)

        doc_count = len(payload.documents)
        query_tokens = len(payload.query.split())
        usage = {"prompt_tokens": query_tokens + doc_count * 50}
        await record_router_usage(
            request.app.state.router_state_backend,
            served_deployment.deployment_id,
            mode="rerank",
            usage={"rerank_units": doc_count},
        )
        pricing = resolve_deployment_tier_pricing(
            auth=auth,
            model=payload.model,
            deployment=served_deployment,
            tier_policy_service=getattr(request.app.state, "tier_policy_service", None),
            mode="sync",
        )
        customer_billing = resolve_token_billing_result(
            pricing,
            model=payload.model,
            usage=usage,
        )
        provider_billing = resolve_token_billing_result(
            pricing,
            model=payload.model,
            usage=usage,
            pricing_view="provider",
        )
        request_cost = customer_billing.billing.cost
        provider_cost = (
            None
            if provider_billing.billing.unpriced_reason is not None
            else provider_billing.billing.cost
        )
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
                call_type="rerank",
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
                        billing=customer_billing.billing,
                        provider_billing=provider_billing.billing,
                        effective_pricing_sources=(
                            customer_billing.pricing_sources_used
                        ),
                        missing_pricing_fields=(
                            customer_billing.missing_pricing_fields
                        ),
                    ),
                    request,
                ),
                cache_hit=False,
                start_time=callback_start,
                end_time=datetime.now(tz=UTC),
            )
        )
        observe_request_latency(model=payload.model, api_provider=api_provider, status_code=200, latency_seconds=perf_counter() - request_start)
        observe_api_latency(model=payload.model, api_provider=api_provider, latency_seconds=api_latency_ms / 1000)
        callback_payload = build_standard_logging_payload(
            call_type="rerank", request_id=request_id, model=payload.model,
            deployment_model=deployment_model, request_payload=request_data, response_obj=data,
            user_api_key_dict=auth.model_dump(mode="json"), start_time=callback_start, end_time=datetime.now(tz=UTC),
            api_base=api_base, response_cost=request_cost, api_latency_ms=api_latency_ms,
            api_provider=api_provider,
            turn_off_message_logging=bool(getattr(request.app.state, "turn_off_message_logging", False)),
        )
        callback_manager.dispatch_success_callbacks(callback_payload)
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.RERANK_REQUEST,
            status="success",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="model",
            resource_id=payload.model,
            request_payload=request_data,
            response_payload=data,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            metadata=attach_route_decision(
                {
                    "route": request.url.path,
                    "provider": api_provider,
                    "api_base": api_base,
                    "deployment_model": deployment_model,
                    "document_count": doc_count,
                },
                request,
            ),
        )
        return JSONResponse(status_code=200, content=data, headers=route_decision_headers(request))
    except httpx.HTTPError as exc:
        failure_target = resolve_failure_target(request, fallback_deployment=primary)
        failure_deployment_id = str(failure_target.deployment_id or primary.deployment_id)
        failure_provider = str(failure_target.provider or api_provider)
        failure_api_base = failure_target.api_base or primary_api_base
        failure_deployment_model = failure_target.deployment_model or primary.deltallm_params.get("model")
        await request.app.state.passive_health_tracker.record_request_outcome(
            failure_deployment_id,
            success=False,
            error=str(exc),
            exc=exc,
        )
        status_code = getattr(getattr(exc, "response", None), "status_code", 502)
        increment_request(model=payload.model, api_provider=failure_provider, api_key=auth.api_key, user=auth.user_id, team=auth.team_id, status_code=status_code)
        increment_request_failure(model=payload.model, api_provider=failure_provider, error_type=exc.__class__.__name__)
        observe_request_latency(model=payload.model, api_provider=failure_provider, status_code=status_code, latency_seconds=perf_counter() - request_start)
        enqueue_request_log_write(
            request,
            request.app.state.spend_tracking_service.log_request_failure(
                request_id=request_id or "",
                api_key=auth.api_key,
                user_id=auth.user_id,
                team_id=auth.team_id,
                organization_id=getattr(auth, "organization_id", None),
                end_user_id=None,
                model=payload.model,
                call_type="rerank",
                metadata=attach_route_decision(
                    {
                        "route": request.url.path,
                        "provider": failure_provider,
                        "api_base": failure_api_base,
                        "deployment_model": failure_deployment_model,
                        "document_count": len(payload.documents),
                    },
                    request,
                ),
                cache_hit=False,
                start_time=callback_start,
                end_time=datetime.now(tz=UTC),
                http_status_code=status_code,
                exc=exc,
            )
        )
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.RERANK_REQUEST,
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
                    "document_count": len(payload.documents),
                },
                request,
            ),
        )
        raise InvalidRequestError(message=f"Rerank request failed: {exc}") from exc
    except Exception as exc:
        failure_target = resolve_failure_target(request, fallback_deployment=primary)
        failure_deployment_id = str(failure_target.deployment_id or primary.deployment_id)
        failure_provider = str(failure_target.provider or api_provider)
        failure_api_base = failure_target.api_base or primary_api_base
        failure_deployment_model = failure_target.deployment_model or primary.deltallm_params.get("model")
        await request.app.state.passive_health_tracker.record_request_outcome(
            failure_deployment_id,
            success=False,
            error=str(exc),
            exc=exc,
        )
        status_code = int(getattr(exc, "status_code", 500) or 500)
        enqueue_request_log_write(
            request,
            request.app.state.spend_tracking_service.log_request_failure(
                request_id=request_id or "",
                api_key=auth.api_key,
                user_id=auth.user_id,
                team_id=auth.team_id,
                organization_id=getattr(auth, "organization_id", None),
                end_user_id=None,
                model=payload.model,
                call_type="rerank",
                metadata=attach_route_decision(
                    {
                        "route": request.url.path,
                        "provider": failure_provider,
                        "api_base": failure_api_base,
                        "deployment_model": failure_deployment_model,
                        "document_count": len(payload.documents),
                    },
                    request,
                ),
                cache_hit=False,
                start_time=callback_start,
                end_time=datetime.now(tz=UTC),
                http_status_code=status_code,
                exc=exc,
            )
        )
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.RERANK_REQUEST,
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
                    "document_count": len(payload.documents),
                },
                request,
            ),
        )
        raise
