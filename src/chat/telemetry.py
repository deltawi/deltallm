from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from fastapi import Request

from src.billing.cost import completion_cost
from src.billing.pricing import pricing_from_model_info
from src.callbacks import build_standard_logging_payload
from src.chat.audit import emit_text_audit_event
from src.metrics import (
    increment_request,
    increment_request_failure,
    increment_spend,
    increment_usage,
    observe_api_latency,
    observe_request_latency,
)
from src.providers.resolution import resolve_provider
from src.router.health_policy import exception_status_code
from src.telemetry.request_failures import enqueue_request_log_write
from src.routers.routing_decision import attach_route_decision, resolve_failure_target
from src.routers.utils import fire_and_forget


def _append_route_decision_metadata(request: Request, metadata: dict[str, Any]) -> dict[str, Any]:
    return attach_route_decision(metadata, request)


def _resolve_failure_fields(
    request: Request,
    *,
    fallback_deployment: Any,
    default_provider: str | None = None,
    default_api_base: str | None = None,
    default_deployment_model: str | None = None,
) -> dict[str, str | None]:
    target = resolve_failure_target(request, fallback_deployment=fallback_deployment)
    fallback_params = getattr(fallback_deployment, "deltallm_params", {})
    fallback_model = fallback_params.get("model") if isinstance(fallback_params, dict) else None
    return {
        "deployment_id": target.deployment_id or getattr(fallback_deployment, "deployment_id", None),
        "provider": target.provider or default_provider,
        "api_base": target.api_base or default_api_base,
        "deployment_model": target.deployment_model or default_deployment_model or fallback_model,
    }


async def emit_stream_success(
    *,
    request: Request,
    auth: Any,
    payload: Any,
    request_data: dict[str, Any],
    callback_manager: Any,
    guardrail_middleware: Any,
    callback_start: datetime,
    request_start: float,
    request_id: str | None,
    stream_response_object: str,
    cache_hit: bool,
    cache_key: str | None,
    audit_action: str,
    served_deployment: Any,
    api_base: str,
    params: dict[str, Any],
) -> None:
    await request.app.state.passive_health_tracker.record_request_outcome(served_deployment.deployment_id, success=True)
    callback_payload = build_standard_logging_payload(
        call_type="completion",
        request_id=request_id,
        model=payload.model,
        deployment_model=params.get("model"),
        request_payload=request_data,
        response_obj={"object": stream_response_object},
        user_api_key_dict=auth.model_dump(mode="json"),
        start_time=callback_start,
        end_time=datetime.now(tz=UTC),
        api_base=api_base,
        cache_hit=cache_hit,
        cache_key=cache_key,
        turn_off_message_logging=bool(getattr(request.app.state, "turn_off_message_logging", False)),
    )
    callback_manager.dispatch_success_callbacks(callback_payload)
    await callback_manager.execute_post_call_success_hooks(
        data=request_data,
        user_api_key_dict=auth.model_dump(mode="json"),
        response={"object": stream_response_object},
    )
    await guardrail_middleware.run_post_call_success(
        request_data=request_data,
        user_api_key_dict=auth.model_dump(mode="python"),
        response_data={"object": stream_response_object},
        call_type="completion",
    )
    emit_text_audit_event(
        request=request,
        auth=auth,
        action=audit_action,
        model=payload.model,
        status="success",
        request_start=request_start,
        request_data=request_data,
        response_data={"object": stream_response_object},
        metadata=_append_route_decision_metadata(
            request,
            {
                "route": request.url.path,
                "stream": True,
                "cache_hit": cache_hit,
                "cache_key": cache_key,
                "api_base": api_base,
                "provider": resolve_provider(params),
                "deployment_model": params.get("model"),
            },
        ),
    )


async def emit_stream_failure(
    *,
    request: Request,
    auth: Any,
    payload: Any,
    request_data: dict[str, Any],
    callback_manager: Any,
    guardrail_middleware: Any,
    callback_start: datetime,
    request_start: float,
    request_id: str | None,
    cache_hit: bool,
    cache_key: str | None,
    audit_action: str,
    primary_deployment: Any,
    api_base: str,
    params: dict[str, Any],
    exc: Exception,
) -> None:
    failure_fields = _resolve_failure_fields(
        request,
        fallback_deployment=primary_deployment,
        default_provider=resolve_provider(params),
        default_api_base=api_base,
        default_deployment_model=params.get("model"),
    )
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
            call_type="completion",
            metadata=_append_route_decision_metadata(
                request,
                {
                    "route": request.url.path,
                    "stream": True,
                    "cache_hit": cache_hit,
                    "cache_key": cache_key,
                    "api_base": failure_fields["api_base"],
                    "provider": failure_fields["provider"],
                    "deployment_model": failure_fields["deployment_model"],
                },
            ),
            cache_hit=cache_hit,
            start_time=callback_start,
            end_time=datetime.now(tz=UTC),
            http_status_code=exception_status_code(exc),
            exc=exc,
        )
    )
    if failure_fields["deployment_id"]:
        await request.app.state.passive_health_tracker.record_request_outcome(
            str(failure_fields["deployment_id"]),
            success=False,
            error=str(exc),
            exc=exc,
        )
    callback_payload = build_standard_logging_payload(
        call_type="completion",
        request_id=request_id,
        model=payload.model,
        deployment_model=failure_fields["deployment_model"],
        request_payload=request_data,
        response_obj=None,
        user_api_key_dict=auth.model_dump(mode="json"),
        start_time=callback_start,
        end_time=datetime.now(tz=UTC),
        api_base=failure_fields["api_base"],
        cache_hit=cache_hit,
        cache_key=cache_key,
        error_info={
            "error_type": getattr(exc, "error_type", None) or exc.__class__.__name__,
            "message": str(exc),
        },
        turn_off_message_logging=bool(getattr(request.app.state, "turn_off_message_logging", False)),
    )
    callback_manager.dispatch_failure_callbacks(callback_payload, exc)
    await callback_manager.execute_post_call_failure_hooks(
        request_data=request_data,
        original_exception=exc,
        user_api_key_dict=auth.model_dump(mode="json"),
    )
    await guardrail_middleware.run_post_call_failure(
        request_data=request_data,
        user_api_key_dict=auth.model_dump(mode="python"),
        original_exception=exc,
        call_type="completion",
    )
    emit_text_audit_event(
        request=request,
        auth=auth,
        action=audit_action,
        model=payload.model,
        status="error",
        request_start=request_start,
        request_data=request_data,
        response_data=None,
        error=exc,
        metadata=_append_route_decision_metadata(
            request,
            {
                "route": request.url.path,
                "stream": True,
                "cache_hit": cache_hit,
                "cache_key": cache_key,
                "api_base": failure_fields["api_base"],
                "provider": failure_fields["provider"],
                "deployment_model": failure_fields["deployment_model"],
            },
        ),
    )


async def emit_nonstream_success(
    *,
    request: Request,
    auth: Any,
    payload: Any,
    payload_data: dict[str, Any],
    response_payload: dict[str, Any],
    served_deployment: Any,
    api_latency_ms: float,
    callback_manager: Any,
    guardrail_middleware: Any,
    request_data: dict[str, Any],
    callback_start: datetime,
    request_start: float,
    request_id: str | None,
    cache_hit: bool,
    cache_key: str | None,
    audit_action: str,
) -> None:
    await request.app.state.passive_health_tracker.record_request_outcome(served_deployment.deployment_id, success=True)
    api_provider = resolve_provider(served_deployment.deltallm_params)
    api_base = str(served_deployment.deltallm_params.get("api_base", request.app.state.settings.openai_base_url)).rstrip("/")
    usage = payload_data.get("usage") or {}
    deploy_pricing = pricing_from_model_info(
        served_deployment.model_info,
        fallback_input_cost_per_token=served_deployment.input_cost_per_token,
        fallback_output_cost_per_token=served_deployment.output_cost_per_token,
    )
    request_cost = completion_cost(
        model=payload.model,
        usage=usage,
        cache_hit=getattr(request.state, "cache_hit", False),
        custom_pricing=deploy_pricing,
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
    fire_and_forget(
        request.app.state.spend_tracking_service.log_spend(
            request_id=request_id or "",
            api_key=auth.api_key,
            user_id=auth.user_id,
            team_id=auth.team_id,
            organization_id=getattr(auth, "organization_id", None),
            end_user_id=None,
            model=payload.model,
            call_type="completion",
            usage=usage,
            cost=request_cost,
            metadata=_append_route_decision_metadata(
                request,
                {
                    "api_base": api_base,
                    "provider": api_provider,
                    "deployment_model": served_deployment.deltallm_params.get("model"),
                },
            ),
            cache_hit=cache_hit,
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
    observe_api_latency(model=payload.model, api_provider=api_provider, latency_seconds=api_latency_ms / 1000)
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    callback_payload = build_standard_logging_payload(
        call_type="completion",
        request_id=request_id,
        model=payload.model,
        deployment_model=served_deployment.deltallm_params.get("model"),
        request_payload=request_data,
        response_obj=response_payload,
        user_api_key_dict=auth.model_dump(mode="json"),
        start_time=callback_start,
        end_time=datetime.now(tz=UTC),
        api_base=api_base,
        cache_hit=cache_hit,
        cache_key=cache_key,
        response_cost=request_cost,
        api_latency_ms=api_latency_ms,
        turn_off_message_logging=bool(getattr(request.app.state, "turn_off_message_logging", False)),
    )
    callback_manager.dispatch_success_callbacks(callback_payload)
    await callback_manager.execute_post_call_success_hooks(
        data=request_data,
        user_api_key_dict=auth.model_dump(mode="json"),
        response=response_payload,
    )
    await guardrail_middleware.run_post_call_success(
        request_data=request_data,
        user_api_key_dict=auth.model_dump(mode="python"),
        response_data=response_payload,
        call_type="completion",
    )
    emit_text_audit_event(
        request=request,
        auth=auth,
        action=audit_action,
        model=payload.model,
        status="success",
        request_start=request_start,
        request_data=request_data,
        response_data=response_payload,
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
        metadata=_append_route_decision_metadata(
            request,
            {
                "route": request.url.path,
                "stream": False,
                "cache_hit": cache_hit,
                "cache_key": cache_key,
                "api_base": api_base,
                "provider": api_provider,
                "deployment_model": served_deployment.deltallm_params.get("model"),
            },
        ),
    )


async def emit_nonstream_failure(
    *,
    request: Request,
    auth: Any,
    payload: Any,
    primary_deployment: Any,
    callback_manager: Any,
    guardrail_middleware: Any,
    request_data: dict[str, Any],
    callback_start: datetime,
    request_start: float,
    request_id: str | None,
    cache_hit: bool,
    cache_key: str | None,
    audit_action: str,
    api_provider: str,
    api_base: str,
    exc: Exception,
    status_code: int,
) -> None:
    failure_fields = _resolve_failure_fields(
        request,
        fallback_deployment=primary_deployment,
        default_provider=api_provider,
        default_api_base=api_base,
        default_deployment_model=primary_deployment.deltallm_params.get("model"),
    )
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
            call_type="completion",
            metadata=_append_route_decision_metadata(
                request,
                {
                    "route": request.url.path,
                    "stream": False,
                    "cache_hit": cache_hit,
                    "cache_key": cache_key,
                    "api_base": failure_fields["api_base"],
                    "provider": failure_fields["provider"],
                    "deployment_model": failure_fields["deployment_model"],
                },
            ),
            cache_hit=cache_hit,
            start_time=callback_start,
            end_time=datetime.now(tz=UTC),
            http_status_code=status_code,
            exc=exc,
        )
    )
    await guardrail_middleware.run_post_call_failure(
        request_data=request_data,
        user_api_key_dict=auth.model_dump(mode="python"),
        original_exception=exc,
        call_type="completion",
    )
    await request.app.state.passive_health_tracker.record_request_outcome(
        str(failure_fields["deployment_id"] or primary_deployment.deployment_id),
        success=False,
        error=str(exc),
        exc=exc,
    )
    increment_request(
        model=payload.model,
        api_provider=str(failure_fields["provider"] or api_provider),
        api_key=auth.api_key,
        user=auth.user_id,
        team=auth.team_id,
        status_code=status_code,
    )
    increment_request_failure(
        model=payload.model,
        api_provider=str(failure_fields["provider"] or api_provider),
        error_type=exc.__class__.__name__,
    )
    observe_request_latency(
        model=payload.model,
        api_provider=str(failure_fields["provider"] or api_provider),
        status_code=status_code,
        latency_seconds=perf_counter() - request_start,
    )
    callback_payload = build_standard_logging_payload(
        call_type="completion",
        request_id=request_id,
        model=payload.model,
        deployment_model=failure_fields["deployment_model"],
        request_payload=request_data,
        response_obj=None,
        user_api_key_dict=auth.model_dump(mode="json"),
        start_time=callback_start,
        end_time=datetime.now(tz=UTC),
        api_base=failure_fields["api_base"],
        cache_hit=cache_hit,
        cache_key=cache_key,
        error_info={"error_type": exc.__class__.__name__, "message": str(exc)},
        turn_off_message_logging=bool(getattr(request.app.state, "turn_off_message_logging", False)),
    )
    callback_manager.dispatch_failure_callbacks(callback_payload, exc)
    await callback_manager.execute_post_call_failure_hooks(
        request_data=request_data,
        original_exception=exc,
        user_api_key_dict=auth.model_dump(mode="json"),
    )
    emit_text_audit_event(
        request=request,
        auth=auth,
        action=audit_action,
        model=payload.model,
        status="error",
        request_start=request_start,
        request_data=request_data,
        response_data=None,
        error=exc,
        metadata=_append_route_decision_metadata(
            request,
            {
                "route": request.url.path,
                "stream": False,
                "cache_hit": cache_hit,
                "cache_key": cache_key,
                "api_base": failure_fields["api_base"],
                "provider": failure_fields["provider"],
                "deployment_model": failure_fields["deployment_model"],
            },
        ),
    )
