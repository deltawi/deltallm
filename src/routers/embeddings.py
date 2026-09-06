from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.audit.delivery import AuditDeliveryClass
from src.billing.tier_pricing import (
    attach_pricing_metadata,
    resolve_deployment_tier_pricing,
    resolve_token_billing_result,
)
from src.cache.pricing import cache_pricing_snapshot_from_deployment
from src.callbacks import build_standard_logging_payload
from src.router.runtime_generation import pin_routing_runtime_generation
from src.embedding_preflight import run_embedding_preflight
from src.middleware.auth import require_api_key
from src.metrics import (
    increment_request,
    increment_request_failure,
    increment_spend,
    increment_usage,
    observe_api_latency,
    observe_request_latency,
)
from src.models.errors import InvalidRequestError
from src.models.requests import EmbeddingRequest
from src.providers.base import (
    is_valid_provider_number,
    is_valid_provider_token_count,
    parse_provider_json_response,
    validate_provider_success_payload,
)
from src.providers.resolution import resolve_provider, resolve_upstream_model
from src.router import ROUTING_MODE_CONTEXT_KEY, require_initial_deployment
from src.router.context_policy import RequestTokenDemand, set_request_token_demand
from src.upstream_auth import build_openai_compatible_auth_headers
from src.router.router import Deployment
from src.router.usage import record_router_usage
from src.telemetry.request_failures import enqueue_request_log_write, seed_request_failure_context
from src.telemetry.event_identity import get_or_create_billing_event_id
from src.upstream_http import build_upstream_request_timeout_for_request, configured_timeout_seconds
from src.routers.routing_decision import (
    capture_attempted_deployment,
    capture_initial_route_decision,
    route_failover_kwargs,
    route_decision_headers,
    route_decision_metadata,
    resolve_failure_target,
    update_served_route_decision,
)
from src.services.audit_service import (
    AuditEventInput,
    AuditPayloadInput,
    AuditService,
    enqueue_audit_event,
)
from src.audit.actions import AuditAction
from src.audit.errors import derive_audit_error_code

router = APIRouter(prefix="/v1", tags=["embeddings"])


def _is_valid_embedding_success_payload(
    data: Mapping[str, Any],
    *,
    expected_count: int,
    expected_dimensions: int | None,
    encoding_format: str,
) -> bool:
    embeddings = data.get("data")
    usage = data.get("usage")
    if (
        not isinstance(embeddings, list)
        or len(embeddings) != expected_count
        or not isinstance(usage, Mapping)
    ):
        return False
    indexes: set[int] = set()
    for item in embeddings:
        if not isinstance(item, Mapping):
            return False
        index = item.get("index")
        embedding = item.get("embedding")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
            or index >= expected_count
            or index in indexes
        ):
            return False
        indexes.add(index)
        if encoding_format == "base64":
            if not isinstance(embedding, str) or not embedding:
                return False
        elif (
            not isinstance(embedding, list)
            or not embedding
            or (expected_dimensions is not None and len(embedding) != expected_dimensions)
            or not all(is_valid_provider_number(component) for component in embedding)
        ):
            return False
    prompt_tokens = usage.get("prompt_tokens")
    total_tokens = usage.get("total_tokens")
    return (
        indexes == set(range(expected_count))
        and is_valid_provider_token_count(prompt_tokens)
        and is_valid_provider_token_count(total_tokens)
        and total_tokens >= prompt_tokens
    )


def _embedding_input_count(value: str | list[str] | list[int] | list[list[int]]) -> int:
    if isinstance(value, str):
        return 1
    if value and all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        return 1
    return len(value)


def _request_client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_hop = forwarded_for.split(",", 1)[0].strip()
        if first_hop:
            return first_hop
    if request.client and request.client.host:
        return request.client.host
    return None


async def _emit_embedding_audit_event(
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

    await enqueue_audit_event(
        audit_service,
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
            error_code=derive_audit_error_code(error),
            metadata=metadata or {},
        ),
        payloads=payloads,
        delivery_class=AuditDeliveryClass.REQUIRED,
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
    provider = resolve_provider(params)
    headers = build_openai_compatible_auth_headers(
        provider=provider,
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
        f"{api_base}/embeddings",
        headers=headers,
        json=upstream_payload,
        timeout=build_upstream_request_timeout_for_request(
            request, configured_timeout_seconds(params.get("timeout"))
        ),
    )
    if response.status_code >= 400:
        status_error = httpx.HTTPStatusError(
            f"Upstream embedding call failed with status {response.status_code}",
            request=httpx.Request("POST", f"{api_base}/embeddings"),
            response=response,
        )
        raise request.app.state.provider_error_mapper_registry.map_error(provider, status_error)
    data = parse_provider_json_response(response)
    validate_provider_success_payload(
        data,
        lambda response: _is_valid_embedding_success_payload(
            response,
            expected_count=_embedding_input_count(payload.input),
            expected_dimensions=payload.dimensions,
            encoding_format=payload.encoding_format or "float",
        ),
    )
    api_latency_ms = (perf_counter() - upstream_start) * 1000
    data["_api_latency_ms"] = api_latency_ms
    data["_api_base"] = api_base
    data["_deployment_model"] = params.get("model")
    return data


@router.post("/embeddings", dependencies=[Depends(require_api_key)])
async def embeddings(request: Request, payload: EmbeddingRequest):
    request_start = perf_counter()
    callback_start = datetime.now(tz=UTC)
    seed_request_failure_context(
        request,
        call_type="embedding",
        model=payload.model,
        request_start=request_start,
        audit_action=AuditAction.EMBEDDING_REQUEST.value,
    )
    routing_runtime = pin_routing_runtime_generation(request.app.state, request.state)
    preflight = await run_embedding_preflight(
        request=request,
        payload=payload,
        routing_runtime=routing_runtime,
    )
    auth = preflight.auth
    payload = preflight.payload
    request_data = preflight.request_data
    callback_manager = preflight.callback_manager

    app_router = routing_runtime.router
    model_group = app_router.resolve_model_group(payload.model)
    request_context = {
        "metadata": {},
        "user_id": auth.user_id or auth.api_key,
        ROUTING_MODE_CONTEXT_KEY: "embedding",
    }
    set_request_token_demand(
        request_context,
        RequestTokenDemand(
            input_tokens=preflight.context_input_tokens,
            requested_output_tokens=0,
        ),
    )
    primary = await require_initial_deployment(
        router=app_router,
        failover_manager=routing_runtime.failover_manager,
        model_group=model_group,
        request_context=request_context,
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
            execute=lambda dep: _execute_embedding(request, payload, dep),
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
        request.state.cache_store_pricing = cache_pricing_snapshot_from_deployment(
            served_deployment
        )
        request.state.cache_store_deployment_id = served_deployment.deployment_id
        request.state.cache_store_provider = resolve_provider(served_deployment.deltallm_params)
        request.state.cache_store_deployment_model = (
            str(served_deployment.deltallm_params.get("model") or "") or None
        )
        route_meta = route_decision_metadata(request)
        api_provider = resolve_provider(served_deployment.deltallm_params)

        api_latency_ms = data.pop("_api_latency_ms", 0)
        api_base = data.pop("_api_base", "")
        deployment_model = data.pop("_deployment_model", None)

        usage = data.get("usage") or {}
        await record_router_usage(
            request.app.state.router_state_backend,
            served_deployment.deployment_id,
            mode="embedding",
            usage=usage,
        )
        pricing = resolve_deployment_tier_pricing(
            auth=auth,
            model=payload.model,
            deployment=served_deployment,
            tier_policy_service=getattr(request.app.state, "tier_policy_service", None),
            mode="sync",
        )
        cache_hit = bool(getattr(request.state, "cache_hit", False))
        customer_billing = resolve_token_billing_result(
            pricing,
            model=payload.model,
            usage=usage,
            cache_hit=cache_hit,
        )
        provider_billing = resolve_token_billing_result(
            pricing,
            model=payload.model,
            usage=usage,
            cache_hit=cache_hit,
            pricing_view="provider",
        )
        request_cost = customer_billing.billing.cost
        provider_cost = (
            None
            if provider_billing.billing.unpriced_reason is not None
            else provider_billing.billing.cost
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
        spend_metadata: dict[str, Any] = {
            "api_base": api_base,
            "provider": api_provider,
            "deployment_model": deployment_model,
        }
        if route_meta is not None:
            spend_metadata["routing_decision"] = route_meta
        spend_metadata = attach_pricing_metadata(
            spend_metadata,
            pricing,
            provider_cost=provider_cost,
            billing=customer_billing.billing,
            provider_billing=provider_billing.billing,
            effective_pricing_sources=customer_billing.pricing_sources_used,
            missing_pricing_fields=customer_billing.missing_pricing_fields,
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
                call_type="embedding",
                usage=usage,
                cost=request_cost,
                metadata=spend_metadata,
                cache_hit=cache_hit,
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
            api_provider=api_provider,
            turn_off_message_logging=bool(
                getattr(request.app.state, "turn_off_message_logging", False)
            ),
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
        await _emit_embedding_audit_event(
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
        callback_payload = build_standard_logging_payload(
            call_type="embedding",
            request_id=request_id,
            model=payload.model,
            deployment_model=failure_deployment_model,
            request_payload=request_data,
            response_obj=None,
            user_api_key_dict=auth.model_dump(mode="json"),
            start_time=callback_start,
            end_time=datetime.now(tz=UTC),
            api_base=failure_api_base,
            api_provider=failure_provider,
            error_info={"error_type": exc.__class__.__name__, "message": str(exc)},
            turn_off_message_logging=bool(
                getattr(request.app.state, "turn_off_message_logging", False)
            ),
        )
        callback_manager.dispatch_failure_callbacks(callback_payload, exc)
        await request.app.state.guardrail_middleware.run_post_call_failure(
            request_data=request_data,
            user_api_key_dict=auth.model_dump(mode="python"),
            original_exception=exc,
            call_type="embedding",
        )
        error_route_meta = route_decision_metadata(request)
        error_metadata: dict[str, Any] = {
            "route": request.url.path,
            "api_base": failure_api_base,
            "provider": failure_provider,
            "deployment_model": failure_deployment_model,
        }
        if error_route_meta is not None:
            error_metadata["routing_decision"] = error_route_meta
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
                call_type="embedding",
                metadata=error_metadata,
                cache_hit=bool(getattr(request.state, "cache_hit", False)),
                start_time=callback_start,
                end_time=datetime.now(tz=UTC),
                http_status_code=status_code,
                exc=exc,
            ),
        )
        await _emit_embedding_audit_event(
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
        failure_target = resolve_failure_target(request, fallback_deployment=primary)
        failure_provider = str(failure_target.provider or api_provider)
        failure_api_base = failure_target.api_base or primary_api_base
        failure_deployment_model = failure_target.deployment_model or primary.deltallm_params.get(
            "model"
        )
        status_code = int(getattr(exc, "status_code", 500) or 500)
        callback_payload = build_standard_logging_payload(
            call_type="embedding",
            request_id=request_id,
            model=payload.model,
            deployment_model=failure_deployment_model,
            request_payload=request_data,
            response_obj=None,
            user_api_key_dict=auth.model_dump(mode="json"),
            start_time=callback_start,
            end_time=datetime.now(tz=UTC),
            api_base=failure_api_base,
            api_provider=failure_provider,
            error_info={"error_type": exc.__class__.__name__, "message": str(exc)},
            turn_off_message_logging=bool(
                getattr(request.app.state, "turn_off_message_logging", False)
            ),
        )
        callback_manager.dispatch_failure_callbacks(callback_payload, exc)
        await request.app.state.guardrail_middleware.run_post_call_failure(
            request_data=request_data,
            user_api_key_dict=auth.model_dump(mode="python"),
            original_exception=exc,
            call_type="embedding",
        )
        error_route_meta = route_decision_metadata(request)
        error_metadata: dict[str, Any] = {
            "route": request.url.path,
            "api_base": failure_api_base,
            "provider": failure_provider,
            "deployment_model": failure_deployment_model,
        }
        if error_route_meta is not None:
            error_metadata["routing_decision"] = error_route_meta
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
                call_type="embedding",
                metadata=error_metadata,
                cache_hit=bool(getattr(request.state, "cache_hit", False)),
                start_time=callback_start,
                end_time=datetime.now(tz=UTC),
                http_status_code=status_code,
                exc=exc,
            ),
        )
        await _emit_embedding_audit_event(
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
