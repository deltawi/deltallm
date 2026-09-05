from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
from time import perf_counter
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.cache.pricing import cache_pricing_snapshot_from_deployment
from src.cache.streaming import StreamWriteContext
from src.chat import (
    audit_action_for_path,
    emit_nonstream_success,
    emit_precommit_failure,
    emit_stream_failure,
    emit_stream_success,
    execute_chat,
    open_stream_with_first_chunk,
    run_text_preflight,
)
from src.chat.audit import request_client_ip
from src.chat.mcp_execution import (
    MCPChatExecutionService,
    MCPModelRouting,
)
from src.chat.stream_usage import StreamUsageTracker
from src.chat.stream_response import (
    DeadlineStreamingResponse,
    ManagedStreamLifecycle,
    close_stream_resources,
)
from src.router.runtime_generation import pin_routing_runtime_generation
from src.middleware.auth import require_api_key
from src.metrics import increment_router_health_update_failure
from src.mcp.orchestrator import (
    MCPChatOrchestrator,
    MCPRequestContext,
    chat_request_has_mcp_tools,
)
from src.models.errors import InvalidRequestError, ProxyError, ServiceUnavailableError
from src.models.requests import ChatCompletionRequest
from src.providers.registry import resolve_chat_upstream
from src.providers.resolution import resolve_provider
from src.router import ROUTING_MODE_CONTEXT_KEY, require_initial_deployment
from src.router.context_policy import RequestTokenDemand, set_request_token_demand
from src.router.health_state import DeploymentHealthRef
from src.router.usage import record_router_usage
from src.telemetry.request_failures import seed_request_failure_context
from src.routers.routing_decision import (
    capture_attempted_deployment,
    capture_initial_route_decision,
    route_failover_kwargs,
    route_decision_headers,
    update_served_route_decision,
)

router = APIRouter(prefix="/v1", tags=["chat"])
logger = logging.getLogger(__name__)


async def _record_stream_health_outcome(
    *,
    cooldown_manager: Any,
    health_ref: DeploymentHealthRef,
    health_error: Exception | None,
    succeeded: bool,
    recovery_token: str | None,
) -> None:
    try:
        if health_error is not None:
            await cooldown_manager.record_failure(
                health_ref,
                str(health_error),
                exc=health_error,
                recovery_token=recovery_token,
            )
        elif succeeded:
            await cooldown_manager.record_success(
                health_ref,
                recovery_token=recovery_token,
            )
    except Exception:
        increment_router_health_update_failure()
        logger.warning(
            "post-stream router health update failed deployment_id=%s",
            health_ref.deployment_id,
            exc_info=True,
        )


@router.post("/chat/completions", dependencies=[Depends(require_api_key)])
async def chat_completions(request: Request, payload: ChatCompletionRequest):
    return await handle_chat_like_request(request, payload)


async def handle_chat_like_request(
    request: Request,
    payload: ChatCompletionRequest,
    *,
    request_data: dict[str, Any] | None = None,
    response_transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    stream_line_transform: Callable[[str], str | None] | None = None,
    stream_error_transform: Callable[[ProxyError], str] | None = None,
    stream_response_object: str = "chat.completion.chunk",
    enable_stream_cache: bool = True,
):
    request_start = perf_counter()
    callback_start = datetime.now(tz=UTC)
    audit_action = audit_action_for_path(request.url.path)
    seed_request_failure_context(
        request,
        call_type="completion",
        model=payload.model,
        request_start=request_start,
        audit_action=audit_action,
    )
    routing_runtime = pin_routing_runtime_generation(request.app.state, request.state)
    preflight = await run_text_preflight(
        request=request,
        payload=payload,
        request_data=request_data,
        routing_runtime=routing_runtime,
    )
    auth = preflight.auth
    payload = preflight.payload
    request_data = preflight.request_data
    callback_manager = preflight.callback_manager
    guardrail_middleware = preflight.guardrail_middleware
    has_mcp_tools = chat_request_has_mcp_tools(payload)

    router = routing_runtime.router
    model_group = router.resolve_model_group(payload.model)
    request_context = {
        "metadata": payload.metadata or {},
        "user_id": auth.user_id or auth.api_key,
        ROUTING_MODE_CONTEXT_KEY: "chat",
    }
    set_request_token_demand(
        request_context,
        RequestTokenDemand(
            input_tokens=preflight.token_estimate,
            requested_output_tokens=payload.max_tokens,
        ),
    )
    primary = await require_initial_deployment(
        router=router,
        failover_manager=routing_runtime.failover_manager,
        model_group=model_group,
        request_context=request_context,
    )
    failover_kwargs = route_failover_kwargs(request_context)
    capture_initial_route_decision(request, request_context)
    api_provider = resolve_provider(primary.deltallm_params)
    request_id = request.headers.get("x-request-id")
    api_base = primary.deltallm_params.get(
        "api_base", request.app.state.settings.openai_base_url
    ).rstrip("/")

    def track_attempt(deployment):  # noqa: ANN001
        capture_attempted_deployment(request, deployment)

    cache_context = getattr(request.state, "cache_context", None)
    cache_hit = bool(getattr(cache_context, "hit", False)) if cache_context is not None else False
    cache_key = getattr(cache_context, "cache_key", None) if cache_context is not None else None
    try:
        if payload.stream:
            if has_mcp_tools:
                raise InvalidRequestError(
                    message="MCP tools are not supported on streaming chat requests yet"
                )
            # Validate provider+mode before starting the streaming response,
            # so unsupported stream providers fail as a normal HTTP error.
            resolve_chat_upstream(request, primary.deltallm_params, is_stream=True)

            managed_stream = await routing_runtime.failover_manager.execute_managed_with_failover(
                primary_deployment=primary,
                model_group=model_group,
                execute=lambda dep: open_stream_with_first_chunk(request, payload, dep),
                on_attempt=track_attempt,
                routing_context=request_context,
                **failover_kwargs,
            )
            opened_stream = managed_stream.value
            stream_lifecycle = ManagedStreamLifecycle(opened_stream, managed_stream)
            served_deployment = managed_stream.deployment
            try:
                update_served_route_decision(
                    request,
                    primary_deployment_id=primary.deployment_id,
                    served_deployment_id=served_deployment.deployment_id,
                )
            except BaseException as exc:
                await close_stream_resources(lambda failure=exc: stream_lifecycle.close(failure))
                raise

            async def stream_sse():
                cache_context = getattr(request.state, "cache_context", None)
                stream_handler = getattr(request.app.state, "streaming_cache_handler", None)
                stream_id = None
                stream_write_context: StreamWriteContext | None = None
                stream_usage = StreamUsageTracker()
                failure_exc: BaseException | None = None
                stream_error: Exception | None = None
                health_error: Exception | None = None
                stream_cache_complete = False
                resolved_usage = None
                try:
                    if (
                        enable_stream_cache
                        and request.url.path == "/v1/chat/completions"
                        and cache_context is not None
                        and stream_handler is not None
                        and cache_context.options.control.value != "no-store"
                    ):
                        cache_ttl = int(
                            cache_context.options.ttl
                            or getattr(
                                getattr(
                                    getattr(request.app.state, "app_config", None),
                                    "general_settings",
                                    None,
                                ),
                                "cache_ttl",
                                3600,
                            )
                        )
                        stream_id = cache_context.cache_key
                        stream_write_context = StreamWriteContext(
                            cache_key=cache_context.cache_key,
                            ttl=cache_ttl,
                            model=payload.model,
                            pricing=cache_pricing_snapshot_from_deployment(served_deployment),
                            deployment_id=served_deployment.deployment_id,
                            provider=resolve_provider(served_deployment.deltallm_params),
                            deployment_model=str(
                                served_deployment.deltallm_params.get("model") or ""
                            )
                            or None,
                        )
                        stream_handler.start_stream(stream_id)

                    initial = opened_stream.first_line
                    if initial:
                        try:
                            line_info = stream_usage.add_line(initial)
                        except Exception as exc:
                            health_error = exc
                            raise
                        if stream_id is not None and stream_handler is not None:
                            stream_handler.add_chunk_from_line(stream_id, initial)
                        if not (
                            line_info.is_usage_only_chunk
                            and not opened_stream.client_stream_usage_requested
                        ):
                            out_line = (
                                stream_line_transform(initial)
                                if stream_line_transform is not None
                                else initial
                            )
                            if out_line is not None:
                                yield f"{out_line}\n\n"
                    stream_iterator = opened_stream.translated_stream.__aiter__()
                    while True:
                        try:
                            line = await anext(stream_iterator)
                        except StopAsyncIteration:
                            break
                        except Exception as exc:
                            if isinstance(exc, ProxyError):
                                health_error = exc
                                raise
                            health_error = opened_stream.adapter.map_error(exc)
                            raise health_error from exc
                        if not line:
                            continue

                        try:
                            line_info = stream_usage.add_line(line)
                        except Exception as exc:
                            health_error = exc
                            raise
                        if stream_id is not None and stream_handler is not None:
                            stream_handler.add_chunk_from_line(stream_id, line)
                            if line.strip() == "data: [DONE]":
                                stream_cache_complete = True

                        if (
                            line_info.is_usage_only_chunk
                            and not opened_stream.client_stream_usage_requested
                        ):
                            continue

                        out_line = (
                            stream_line_transform(line)
                            if stream_line_transform is not None
                            else line
                        )
                        if out_line is None:
                            continue
                        yield f"{out_line}\n\n"
                    resolved_usage = stream_usage.resolve(payload)
                except asyncio.CancelledError as exc:
                    failure_exc = exc
                    raise
                except Exception as exc:
                    stream_error = (
                        exc if isinstance(exc, ProxyError) else health_error or ProxyError()
                    )
                    failure_exc = stream_error
                finally:
                    if (
                        stream_id is not None
                        and stream_handler is not None
                        and (failure_exc is not None or not stream_cache_complete)
                    ):
                        stream_handler.discard_stream(stream_id)
                    try:
                        await _record_stream_health_outcome(
                            cooldown_manager=routing_runtime.cooldown_manager,
                            health_ref=served_deployment.health_ref,
                            health_error=health_error,
                            succeeded=failure_exc is None and resolved_usage is not None,
                            recovery_token=managed_stream.recovery_token,
                        )
                    finally:
                        await close_stream_resources(lambda: stream_lifecycle.close(failure_exc))

                if stream_error is not None:
                    failure_params = opened_stream.params
                    failure_api_base = opened_stream.api_base
                    try:
                        await emit_stream_failure(
                            request=request,
                            auth=auth,
                            payload=payload,
                            request_data=request_data,
                            callback_manager=callback_manager,
                            guardrail_middleware=guardrail_middleware,
                            callback_start=callback_start,
                            request_start=request_start,
                            request_id=request_id,
                            cache_hit=cache_hit,
                            cache_key=cache_key,
                            audit_action=audit_action,
                            primary_deployment=primary,
                            api_base=failure_api_base,
                            params=failure_params,
                            exc=stream_error,
                        )
                    except Exception as reporting_error:
                        logger.warning(
                            "post-stream failure reporting failed deployment_id=%s error_type=%s",
                            served_deployment.deployment_id,
                            type(reporting_error).__name__,
                        )
                    if stream_error_transform is not None:
                        yield f"{stream_error_transform(stream_error)}\n\n"
                    return

                if resolved_usage is None:
                    return
                if (
                    stream_id is not None
                    and stream_handler is not None
                    and stream_write_context is not None
                ):
                    if stream_cache_complete:
                        await stream_handler.finalize_and_store(
                            stream_id,
                            stream_write_context,
                            usage=resolved_usage.usage,
                        )
                    else:
                        stream_handler.discard_stream(stream_id)
                await record_router_usage(
                    request.app.state.router_state_backend,
                    served_deployment.deployment_id,
                    mode="chat",
                    usage=resolved_usage.usage,
                )
                await emit_stream_success(
                    request=request,
                    auth=auth,
                    payload=payload,
                    request_data=request_data,
                    callback_manager=callback_manager,
                    guardrail_middleware=guardrail_middleware,
                    callback_start=callback_start,
                    request_start=request_start,
                    request_id=request_id,
                    stream_response_object=stream_response_object,
                    cache_hit=cache_hit,
                    cache_key=cache_key,
                    audit_action=audit_action,
                    served_deployment=served_deployment,
                    api_base=opened_stream.api_base,
                    params=opened_stream.params,
                    usage=resolved_usage.usage,
                    usage_metadata=resolved_usage.metadata(),
                )

            try:
                response = DeadlineStreamingResponse(
                    stream_sse(),
                    deadline=managed_stream.deadline,
                    close=stream_lifecycle.close,
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "x-deltallm-cache-hit": "false",
                        **route_decision_headers(request),
                    },
                )
            except BaseException as exc:
                await close_stream_resources(lambda failure=exc: stream_lifecycle.close(failure))
                raise
            return response

        route_fallback_used: bool | None = None
        if has_mcp_tools:
            gateway = getattr(request.app.state, "mcp_gateway_service", None)
            if gateway is None:
                raise ServiceUnavailableError(message="MCP gateway service is not available")
            orchestrator = MCPChatOrchestrator(
                gateway,
                audit_service=getattr(request.app.state, "audit_service", None),
            )
            mcp_result = await MCPChatExecutionService(
                failover_manager=routing_runtime.failover_manager,
                orchestrator=orchestrator,
                execute_chat_call=lambda phase_payload, deployment: execute_chat(
                    request,
                    phase_payload,
                    deployment,
                ),
            ).execute(
                request_context=MCPRequestContext(
                    request_headers=dict(request.headers),
                    request_id=request_id,
                    correlation_id=request_id,
                    client_ip=request_client_ip(request),
                    user_agent=request.headers.get("user-agent"),
                ),
                auth=auth,
                payload=payload,
                guardrail_middleware=guardrail_middleware,
                routing=MCPModelRouting(
                    primary_deployment=primary,
                    model_group=model_group,
                    routing_context=request_context,
                    timeout_seconds=failover_kwargs.get("timeout_seconds"),
                    retry_max_attempts=failover_kwargs.get("retry_max_attempts"),
                    retryable_error_classes=tuple(
                        failover_kwargs.get("retryable_error_classes", [])
                    )
                    or None,
                ),
                on_attempt=track_attempt,
            )
            payload_data = mcp_result.payload
            api_latency_ms = mcp_result.api_latency_ms
            served_deployment = mcp_result.served_deployment
            route_fallback_used = mcp_result.fallback_used
        else:
            (
                (payload_data, api_latency_ms),
                served_deployment,
            ) = await routing_runtime.failover_manager.execute_with_failover(
                primary_deployment=primary,
                model_group=model_group,
                execute=lambda deployment: execute_chat(request, payload, deployment),
                return_deployment=True,
                on_attempt=track_attempt,
                routing_context=request_context,
                **failover_kwargs,
            )
        update_served_route_decision(
            request,
            primary_deployment_id=primary.deployment_id,
            served_deployment_id=served_deployment.deployment_id,
            fallback_used=route_fallback_used,
        )
        request.state.cache_store_pricing = cache_pricing_snapshot_from_deployment(
            served_deployment
        )
        request.state.cache_store_deployment_id = served_deployment.deployment_id
        request.state.cache_store_provider = resolve_provider(served_deployment.deltallm_params)
        request.state.cache_store_deployment_model = (
            str(served_deployment.deltallm_params.get("model") or "") or None
        )
        response_payload = (
            response_transform(payload_data) if response_transform is not None else payload_data
        )
        api_provider = resolve_provider(served_deployment.deltallm_params)
        await emit_nonstream_success(
            request=request,
            auth=auth,
            payload=payload,
            payload_data=payload_data,
            response_payload=response_payload,
            served_deployment=served_deployment,
            api_latency_ms=api_latency_ms,
            callback_manager=callback_manager,
            guardrail_middleware=guardrail_middleware,
            request_data=request_data,
            callback_start=callback_start,
            request_start=request_start,
            request_id=request_id,
            cache_hit=cache_hit,
            cache_key=cache_key,
            audit_action=audit_action,
        )
        return JSONResponse(
            status_code=200, content=response_payload, headers=route_decision_headers(request)
        )
    except httpx.HTTPError as exc:
        provider_registry = request.app.state.provider_error_mapper_registry
        mapped_error = provider_registry.map_error(api_provider, exc)
        await emit_precommit_failure(
            request=request,
            auth=auth,
            payload=payload,
            primary_deployment=primary,
            callback_manager=callback_manager,
            guardrail_middleware=guardrail_middleware,
            request_data=request_data,
            callback_start=callback_start,
            request_start=request_start,
            request_id=request_id,
            cache_hit=cache_hit,
            cache_key=cache_key,
            audit_action=audit_action,
            api_provider=api_provider,
            api_base=api_base,
            exc=mapped_error,
            status_code=mapped_error.status_code,
            stream=bool(payload.stream),
        )
        raise mapped_error from exc
    except Exception as exc:
        status_code = int(getattr(exc, "status_code", 500) or 500)
        await emit_precommit_failure(
            request=request,
            auth=auth,
            payload=payload,
            primary_deployment=primary,
            callback_manager=callback_manager,
            guardrail_middleware=guardrail_middleware,
            request_data=request_data,
            callback_start=callback_start,
            request_start=request_start,
            request_id=request_id,
            cache_hit=cache_hit,
            cache_key=cache_key,
            audit_action=audit_action,
            api_provider=api_provider,
            api_base=api_base,
            exc=exc,
            status_code=status_code,
            stream=bool(payload.stream),
        )
        raise
