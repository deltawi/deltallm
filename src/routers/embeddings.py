from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.billing.cost import completion_cost
from src.callbacks import CallbackManager, build_standard_logging_payload
from src.middleware.auth import require_api_key
from src.middleware.rate_limit import enforce_rate_limits
from src.metrics import (
    increment_request,
    increment_request_failure,
    increment_spend,
    increment_usage,
    observe_api_latency,
    observe_request_latency,
)
from src.models.errors import InvalidRequestError, PermissionDeniedError
from src.models.requests import EmbeddingRequest
from src.providers.resolution import resolve_provider, resolve_upstream_model
from src.router.router import Deployment
from src.routers.routing_decision import (
    capture_initial_route_decision,
    route_failover_kwargs,
    route_decision_headers,
    route_decision_metadata,
    update_served_route_decision,
)
from src.routers.utils import enforce_budget_if_configured, fire_and_forget
from src.services.audit_service import AuditEventInput, AuditPayloadInput, AuditService
from src.audit.actions import AuditAction

router = APIRouter(prefix="/v1", tags=["embeddings"])


def _request_client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_hop = forwarded_for.split(",", 1)[0].strip()
        if first_hop:
            return first_hop
    if request.client and request.client.host:
        return request.client.host
    return None


def _emit_embedding_audit_event(
    *,
    request: Request,
    auth: Any,
    model: str,
    request_start: float,
    request_data: dict[str, Any] | None,
    response_data: dict[str, Any] | None,
    status: str,
    error: Exception | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    audit_service: AuditService | None = getattr(request.app.state, "audit_service", None)
    if audit_service is None:
        return

    request_id = request.headers.get("x-request-id")
    payloads = [
        AuditPayloadInput(kind="request", content_json=request_data),
        AuditPayloadInput(kind="response", content_json=response_data),
    ]
    if response_data is None:
        payloads = [AuditPayloadInput(kind="request", content_json=request_data)]

    audit_service.record_event(
        AuditEventInput(
            action=AuditAction.EMBEDDING_REQUEST.value,
            organization_id=getattr(auth, "organization_id", None),
            actor_type="api_key",
            actor_id=getattr(auth, "user_id", None) or getattr(auth, "api_key", None),
            api_key=getattr(auth, "api_key", None),
            resource_type="model",
            resource_id=model,
            request_id=request_id,
            correlation_id=request_id,
            ip=_request_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            status=status,
            latency_ms=int((perf_counter() - request_start) * 1000),
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            error_type=error.__class__.__name__ if error is not None else None,
            error_code=getattr(getattr(error, "response", None), "status_code", None) if error is not None else None,
            metadata=metadata or {},
        ),
        payloads=payloads,
        critical=True,
    )


async def _execute_embedding(
    request: Request,
    payload: EmbeddingRequest,
    deployment: Deployment,
) -> dict[str, Any]:
    params = deployment.deltallm_params
    api_key = params.get("api_key")
    if not api_key:
        raise InvalidRequestError(message="Provider API key is missing for selected model")

    api_base = params.get("api_base", request.app.state.settings.openai_base_url).rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    upstream_payload = payload.model_dump(exclude_none=True)
    upstream_model = resolve_upstream_model(params)
    if upstream_model:
        upstream_payload["model"] = upstream_model

    from src.routers.utils import apply_default_params
    apply_default_params(upstream_payload, deployment.model_info)

    upstream_start = perf_counter()
    response = await request.app.state.http_client.post(
        f"{api_base}/embeddings",
        headers=headers,
        json=upstream_payload,
        timeout=params.get("timeout") or 300,
    )
    if response.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"Upstream embedding call failed with status {response.status_code}",
            request=httpx.Request("POST", f"{api_base}/embeddings"),
            response=response,
        )
    data = response.json()
    api_latency_ms = (perf_counter() - upstream_start) * 1000
    data["_api_latency_ms"] = api_latency_ms
    data["_api_base"] = api_base
    data["_deployment_model"] = params.get("model")
    return data


@router.post("/embeddings", dependencies=[Depends(require_api_key), Depends(enforce_rate_limits)])
async def embeddings(request: Request, payload: EmbeddingRequest):
    request_start = perf_counter()
    callback_start = datetime.now(tz=UTC)
    auth = request.state.user_api_key
    if auth.models and payload.model not in auth.models:
        raise PermissionDeniedError(message=f"Model '{payload.model}' is not allowed for this key")
    await enforce_budget_if_configured(request, model=payload.model, auth=auth)

    callback_manager: CallbackManager = getattr(request.app.state, "callback_manager", CallbackManager())
    request_data = payload.model_dump(exclude_none=True)
    request_data = await callback_manager.execute_pre_call_hooks(
        user_api_key_dict=auth.model_dump(mode="json"),
        cache=getattr(request.state, "cache_context", None),
        data=request_data,
        call_type="embedding",
    )
    request_data = await request.app.state.guardrail_middleware.run_pre_call(
        request_data=request_data,
        user_api_key_dict=auth.model_dump(mode="python"),
        call_type="embedding",
    )
    payload = EmbeddingRequest.model_validate(request_data)

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
            execute=lambda dep: _execute_embedding(request, payload, dep),
            return_deployment=True,
            **failover_kwargs,
        )
        update_served_route_decision(
            request,
            primary_deployment_id=primary.deployment_id,
            served_deployment_id=served_deployment.deployment_id,
        )
        route_meta = route_decision_metadata(request)
        await request.app.state.passive_health_tracker.record_request_outcome(served_deployment.deployment_id, success=True)
        api_provider = resolve_provider(served_deployment.deltallm_params)

        api_latency_ms = data.pop("_api_latency_ms", 0)
        api_base = data.pop("_api_base", "")
        deployment_model = data.pop("_deployment_model", None)

        usage = data.get("usage") or {}
        _deploy_pricing = None
        if served_deployment.input_cost_per_token or served_deployment.output_cost_per_token:
            from src.billing.cost import ModelPricing
            _deploy_pricing = ModelPricing(
                input_cost_per_token=served_deployment.input_cost_per_token,
                output_cost_per_token=served_deployment.output_cost_per_token,
            )
        request_cost = completion_cost(
            model=payload.model,
            usage=usage,
            cache_hit=getattr(request.state, "cache_hit", False),
            custom_pricing=_deploy_pricing,
        )
        increment_request(
            model=payload.model,
            api_provider=api_provider,
            api_key=auth.api_key,
            user=auth.user_id,
            team=auth.team_id,
            status_code=200,
        )
        increment_usage(
            model=payload.model,
            api_provider=api_provider,
            api_key=auth.api_key,
            user=auth.user_id,
            team=auth.team_id,
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        )
        increment_spend(
            model=payload.model,
            api_provider=api_provider,
            api_key=auth.api_key,
            user=auth.user_id,
            team=auth.team_id,
            spend=request_cost,
        )
        spend_metadata: dict[str, Any] = {"api_base": api_base}
        if route_meta is not None:
            spend_metadata["routing_decision"] = route_meta
        fire_and_forget(
            request.app.state.spend_tracking_service.log_spend(
                request_id=request_id or "",
                api_key=auth.api_key,
                user_id=auth.user_id,
                team_id=auth.team_id,
                organization_id=getattr(auth, "organization_id", None),
                end_user_id=None,
                model=payload.model,
                call_type="embedding",
                usage=usage,
                cost=request_cost,
                metadata=spend_metadata,
                cache_hit=getattr(request.state, "cache_hit", False),
                start_time=callback_start,
                end_time=datetime.now(tz=UTC),
            )
        )
        observe_request_latency(
            model=payload.model,
            api_provider=api_provider,
            status_code=200,
            latency_seconds=perf_counter() - request_start,
        )
        observe_api_latency(
            model=payload.model,
            api_provider=api_provider,
            latency_seconds=api_latency_ms / 1000,
        )
        callback_payload = build_standard_logging_payload(
            call_type="embedding",
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
            turn_off_message_logging=bool(getattr(request.app.state, "turn_off_message_logging", False)),
        )
        callback_manager.dispatch_success_callbacks(callback_payload)
        await callback_manager.execute_post_call_success_hooks(
            data=request_data,
            user_api_key_dict=auth.model_dump(mode="json"),
            response=data,
        )
        await request.app.state.guardrail_middleware.run_post_call_success(
            request_data=request_data,
            user_api_key_dict=auth.model_dump(mode="python"),
            response_data=data,
            call_type="embedding",
        )
        audit_metadata: dict[str, Any] = {
            "route": request.url.path,
            "api_base": api_base,
            "provider": api_provider,
            "deployment_model": deployment_model,
        }
        if route_meta is not None:
            audit_metadata["routing_decision"] = route_meta
        _emit_embedding_audit_event(
            request=request,
            auth=auth,
            model=payload.model,
            request_start=request_start,
            request_data=request_data,
            response_data=data,
            status="success",
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            metadata=audit_metadata,
        )
        return JSONResponse(status_code=200, content=data, headers=route_decision_headers(request))
    except httpx.HTTPError as exc:
        await request.app.state.passive_health_tracker.record_request_outcome(primary.deployment_id, success=False, error=str(exc))
        status_code = getattr(getattr(exc, "response", None), "status_code", 502)
        increment_request(
            model=payload.model, api_provider=api_provider,
            api_key=auth.api_key, user=auth.user_id, team=auth.team_id, status_code=status_code,
        )
        increment_request_failure(model=payload.model, api_provider=api_provider, error_type=exc.__class__.__name__)
        observe_request_latency(model=payload.model, api_provider=api_provider, status_code=status_code, latency_seconds=perf_counter() - request_start)
        callback_payload = build_standard_logging_payload(
            call_type="embedding", request_id=request_id, model=payload.model,
            deployment_model=None, request_payload=request_data, response_obj=None,
            user_api_key_dict=auth.model_dump(mode="json"), start_time=callback_start, end_time=datetime.now(tz=UTC),
            api_base=None,
            error_info={"error_type": exc.__class__.__name__, "message": str(exc)},
            turn_off_message_logging=bool(getattr(request.app.state, "turn_off_message_logging", False)),
        )
        callback_manager.dispatch_failure_callbacks(callback_payload, exc)
        await request.app.state.guardrail_middleware.run_post_call_failure(
            request_data=request_data, user_api_key_dict=auth.model_dump(mode="python"),
            original_exception=exc, call_type="embedding",
        )
        error_route_meta = route_decision_metadata(request)
        error_metadata: dict[str, Any] = {
            "route": request.url.path,
            "provider": api_provider,
            "deployment_model": primary.deltallm_params.get("model"),
        }
        if error_route_meta is not None:
            error_metadata["routing_decision"] = error_route_meta
        _emit_embedding_audit_event(
            request=request,
            auth=auth,
            model=payload.model,
            request_start=request_start,
            request_data=request_data,
            response_data=None,
            status="error",
            error=exc,
            metadata=error_metadata,
        )
        raise InvalidRequestError(message=f"Embedding request failed: {exc}") from exc
    except Exception as exc:
        await request.app.state.passive_health_tracker.record_request_outcome(primary.deployment_id, success=False, error=str(exc))
        callback_payload = build_standard_logging_payload(
            call_type="embedding", request_id=request_id, model=payload.model,
            deployment_model=None, request_payload=request_data, response_obj=None,
            user_api_key_dict=auth.model_dump(mode="json"), start_time=callback_start, end_time=datetime.now(tz=UTC),
            api_base=None,
            error_info={"error_type": exc.__class__.__name__, "message": str(exc)},
            turn_off_message_logging=bool(getattr(request.app.state, "turn_off_message_logging", False)),
        )
        callback_manager.dispatch_failure_callbacks(callback_payload, exc)
        await request.app.state.guardrail_middleware.run_post_call_failure(
            request_data=request_data, user_api_key_dict=auth.model_dump(mode="python"),
            original_exception=exc, call_type="embedding",
        )
        error_route_meta = route_decision_metadata(request)
        error_metadata: dict[str, Any] = {
            "route": request.url.path,
            "provider": api_provider,
            "deployment_model": primary.deltallm_params.get("model"),
        }
        if error_route_meta is not None:
            error_metadata["routing_decision"] = error_route_meta
        _emit_embedding_audit_event(
            request=request,
            auth=auth,
            model=payload.model,
            request_start=request_start,
            request_data=request_data,
            response_data=None,
            status="error",
            error=exc,
            metadata=error_metadata,
        )
        raise
