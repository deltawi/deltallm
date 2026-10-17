from __future__ import annotations

import asyncio
import uuid
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
    run_text_preflight,
)
from src.chat.durable_execution import (
    execute_durable_chat as execute_chat,
    open_durable_stream as open_stream_with_first_chunk,
)
from src.chat.audit import request_client_ip
from src.chat.mcp_execution import (
    MCPChatExecutionService,
    MCPModelRouting,
)
from src.chat.mcp_stream import MCPStreamFraming, MCPStreamSession, MCPStreamTelemetry
from src.chat.quality_recording import AttemptQualityRecorder
from src.chat.response_validation import ResponseValidationScope
from src.chat.stream_usage import StreamUsageTracker
from src.chat.stream_response import (
    DeadlineStreamingResponse,
    ManagedStreamLifecycle,
    close_stream_resources,
    record_stream_health_outcome,
)
from src.router.runtime_generation import pin_routing_runtime_generation
from src.middleware.auth import require_api_key
from src.mcp.orchestrator import (
    MCPChatOrchestrator,
    MCPRequestContext,
    chat_request_has_mcp_tools,
)
from src.models.errors import ProxyError, ServiceUnavailableError
from src.models.requests import ChatCompletionRequest
from src.providers.anthropic_messages import NATIVE_MESSAGES_RESPONSE_KEY, NativeMessagesRequest
from src.providers.registry import resolve_chat_upstream
from src.providers.resolution import resolve_provider
from src.router import ROUTING_MODE_CONTEXT_KEY, Deployment, require_initial_deployment
from src.router.execution import RequestDeadline
from src.routers.selector_edge import bind_selector_operation
from src.router.selection.operation import selector_answer_observation
from src.router.context_policy import RequestTokenDemand, set_request_token_demand
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
    native_messages: Callable[[ChatCompletionRequest], NativeMessagesRequest | None] | None = None,
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
    # Built after hooks and guardrails so the native body carries their changes.
    native = native_messages(payload) if native_messages is not None else None
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
            requested_output_tokens=(
                payload.max_completion_tokens
                if payload.max_completion_tokens is not None
                else payload.max_tokens
            ),
        ),
    )
    selector_deadline = bind_selector_operation(
        request,
        runtime=routing_runtime,
        payload=payload,
        auth=auth,
        context=request_context,
        token_estimate=preflight.token_estimate,
    )
    primary = await require_initial_deployment(
        router=router,
        failover_manager=routing_runtime.failover_manager,
        model_group=model_group,
        request_context=request_context,
    )
    failover_kwargs = route_failover_kwargs(request_context)
    if selector_deadline is not None:
        failover_kwargs["request_deadline"] = selector_deadline
    capture_initial_route_decision(request, request_context)
    api_provider = resolve_provider(primary.deltallm_params)
    request_id = request.headers.get("x-request-id")
    api_base = primary.deltallm_params.get(
        "api_base", request.app.state.settings.openai_base_url
    ).rstrip("/")

    def track_attempt(deployment):  # noqa: ANN001
        capture_attempted_deployment(request, deployment)
        observation = selector_answer_observation(request_context)
        if observation is not None:
            observation.attempted(
                deployment.route_group_key or deployment.model_name, deployment.deployment_id
            )

    quality = AttemptQualityRecorder(getattr(router, "quality", None))
    cache_context = getattr(request.state, "cache_context", None)
    cache_hit = bool(getattr(cache_context, "hit", False)) if cache_context is not None else False
    cache_key = getattr(cache_context, "cache_key", None) if cache_context is not None else None
    try:
        if payload.stream:
            # Validate provider+mode before starting the streaming response,
            # so unsupported stream providers fail as a normal HTTP error.
            resolve_chat_upstream(request, primary.deltallm_params, is_stream=True)
            if has_mcp_tools:
                return await _open_mcp_stream(
                    request,
                    MCPStreamTelemetry(
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
                    ),
                    MCPStreamFraming(
                        response_object=stream_response_object,
                        line_transform=stream_line_transform,
                        error_transform=stream_error_transform,
                    ),
                    routing_runtime=routing_runtime,
                    routing=_mcp_model_routing(
                        primary=primary,
                        model_group=model_group,
                        request_context=request_context,
                        selector_deadline=selector_deadline,
                        failover_kwargs=failover_kwargs,
                    ),
                    on_attempt=track_attempt,
                )

            managed_stream = await quality.run(
                routing_runtime.failover_manager.execute_managed_with_failover(
                    primary_deployment=primary,
                    model_group=model_group,
                    execute=lambda dep: quality.opened(
                        dep, open_stream_with_first_chunk(request, payload, dep, native=native)
                    ),
                    on_attempt=track_attempt,
                    routing_context=request_context,
                    **failover_kwargs,
                )
            )
            opened_stream = managed_stream.value
            stream_lifecycle = ManagedStreamLifecycle(opened_stream, managed_stream)
            served_deployment = managed_stream.deployment
            observation = selector_answer_observation(request_context)
            if observation is not None:
                observation.answered(
                    served_deployment.route_group_key or served_deployment.model_name,
                    served_deployment.deployment_id,
                    streaming=True,
                )
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
                terminal_line: str | None = None
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
                        # Identical concurrent streams share a cache key, so each
                        # needs its own accumulator.
                        stream_id = f"{cache_context.cache_key}:{uuid.uuid4().hex}"
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
                        if line_info.is_terminal:
                            terminal_line = initial
                            stream_cache_complete = True
                        elif not (
                            line_info.is_usage_only_chunk
                            and not opened_stream.client_stream_usage_requested
                        ):
                            if (
                                not opened_stream.client_stream_usage_requested
                                and line_info.without_usage is not None
                            ):
                                initial = line_info.without_usage
                            out_line = (
                                stream_line_transform(initial)
                                if stream_line_transform is not None
                                else initial
                            )
                            if out_line is not None:
                                yield f"{out_line}\n\n"
                    stream_iterator = opened_stream.translated_stream.__aiter__()
                    while terminal_line is None:
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
                        if line_info.is_terminal:
                            # A client may close immediately at DONE. Finish accounting
                            # before forwarding it, without waiting for provider EOF.
                            terminal_line = line
                            stream_cache_complete = True
                            break

                        if (
                            line_info.is_usage_only_chunk
                            and not opened_stream.client_stream_usage_requested
                        ):
                            continue

                        if (
                            not opened_stream.client_stream_usage_requested
                            and line_info.without_usage is not None
                        ):
                            line = line_info.without_usage
                        out_line = (
                            stream_line_transform(line)
                            if stream_line_transform is not None
                            else line
                        )
                        if out_line is None:
                            continue
                        yield f"{out_line}\n\n"
                    if opened_stream.output_context is not None:
                        await opened_stream.output_context.finish()
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
                    if opened_stream.output_context is not None:
                        await opened_stream.output_context.finish()
                    if (
                        stream_id is not None
                        and stream_handler is not None
                        and (failure_exc is not None or not stream_cache_complete)
                    ):
                        stream_handler.discard_stream(stream_id)
                    if not isinstance(failure_exc, asyncio.CancelledError):
                        quality.finished(
                            served_deployment,
                            provider_failed=health_error is not None,
                            usage=resolved_usage.usage if resolved_usage is not None else None,
                        )
                    try:
                        await record_stream_health_outcome(
                            cooldown_manager=routing_runtime.cooldown_manager,
                            health_ref=served_deployment.health_ref,
                            health_error=health_error,
                            succeeded=failure_exc is None and resolved_usage is not None,
                            recovery_token=managed_stream.recovery_token,
                        )
                    finally:
                        await close_stream_resources(
                            lambda: stream_lifecycle.close_upstream(failure_exc)
                        )

                if stream_error is not None:
                    await close_stream_resources(lambda: stream_lifecycle.close(failure_exc))
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
                async with emit_stream_success(
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
                ):
                    try:
                        if terminal_line is not None:
                            out_line = (
                                stream_line_transform(terminal_line)
                                if stream_line_transform is not None
                                else terminal_line
                            )
                            if out_line is not None:
                                yield f"{out_line}\n\n"
                    finally:
                        await close_stream_resources(stream_lifecycle.close)

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
        response_validation_settings = (
            routing_runtime.app_config.router_settings.response_validation
        )
        response_validation_headers: dict[str, str] = {}
        if has_mcp_tools:
            orchestrator = _mcp_orchestrator(request)
            mcp_result = await MCPChatExecutionService(
                failover_manager=routing_runtime.failover_manager,
                orchestrator=orchestrator,
                execute_chat_call=lambda phase_payload, deployment: execute_chat(
                    request,
                    phase_payload,
                    deployment,
                ),
            ).execute(
                request_context=_mcp_request_context(request, request_id),
                auth=auth,
                payload=payload,
                guardrail_middleware=guardrail_middleware,
                routing=_mcp_model_routing(
                    primary=primary,
                    model_group=model_group,
                    request_context=request_context,
                    selector_deadline=selector_deadline,
                    failover_kwargs=failover_kwargs,
                ),
                on_attempt=track_attempt,
                response_validation=response_validation_settings,
                quality=quality,
            )
            response_validation_headers = mcp_result.response_validation_headers
            payload_data = mcp_result.payload
            api_latency_ms = mcp_result.api_latency_ms
            served_deployment = mcp_result.served_deployment
            route_fallback_used = mcp_result.fallback_used
        else:
            response_validation = ResponseValidationScope(payload, response_validation_settings)
            (
                (payload_data, api_latency_ms),
                served_deployment,
            ) = await response_validation.run(
                quality.run(
                    routing_runtime.failover_manager.execute_with_failover(
                        primary_deployment=primary,
                        model_group=model_group,
                        execute=lambda deployment: quality.attempt(
                            deployment,
                            response_validation.checked(
                                deployment,
                                execute_chat(request, payload, deployment, native=native),
                            ),
                        ),
                        return_deployment=True,
                        on_attempt=track_attempt,
                        routing_context=request_context,
                        **failover_kwargs,
                    )
                )
            )
            response_validation_headers = response_validation.response_headers()
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
        native_response = payload_data.pop(NATIVE_MESSAGES_RESPONSE_KEY, None)
        if native_response is not None:
            response_payload = native_response
        elif response_transform is not None:
            response_payload = response_transform(payload_data)
        else:
            response_payload = payload_data
        api_provider = resolve_provider(served_deployment.deltallm_params)
        observation = selector_answer_observation(request_context)
        if observation is not None:
            observation.answered(
                served_deployment.route_group_key or served_deployment.model_name,
                served_deployment.deployment_id,
                streaming=False,
            )
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
            status_code=200,
            content=response_payload,
            headers={**route_decision_headers(request), **response_validation_headers},
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


def _mcp_orchestrator(request: Request) -> MCPChatOrchestrator:
    gateway = getattr(request.app.state, "mcp_gateway_service", None)
    if gateway is None:
        raise ServiceUnavailableError(message="MCP gateway service is not available")
    return MCPChatOrchestrator(
        gateway,
        audit_service=getattr(request.app.state, "audit_service", None),
    )


def _mcp_request_context(request: Request, request_id: str | None) -> MCPRequestContext:
    return MCPRequestContext(
        request_headers=dict(request.headers),
        request_id=request_id,
        correlation_id=request_id,
        client_ip=request_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


def _mcp_model_routing(
    *,
    primary: Deployment,
    model_group: str,
    request_context: dict[str, Any],
    selector_deadline: RequestDeadline | None,
    failover_kwargs: dict[str, Any],
) -> MCPModelRouting:
    return MCPModelRouting(
        primary_deployment=primary,
        model_group=model_group,
        routing_context=request_context,
        request_deadline=selector_deadline,
        timeout_seconds=failover_kwargs.get("timeout_seconds"),
        retry_max_attempts=failover_kwargs.get("retry_max_attempts"),
        retryable_error_classes=tuple(failover_kwargs.get("retryable_error_classes", [])) or None,
    )


async def _open_mcp_stream(
    request: Request,
    telemetry: MCPStreamTelemetry,
    framing: MCPStreamFraming,
    *,
    routing_runtime: Any,
    routing: MCPModelRouting,
    on_attempt: Callable[[Deployment], None],
) -> DeadlineStreamingResponse:
    session = MCPStreamSession(
        telemetry=telemetry,
        framing=framing,
        failover_manager=routing_runtime.failover_manager,
        cooldown_manager=routing_runtime.cooldown_manager,
        router_state_backend=request.app.state.router_state_backend,
        orchestrator=_mcp_orchestrator(request),
        request_context=_mcp_request_context(request, telemetry.request_id),
        routing=routing,
        on_attempt=on_attempt,
    )

    def on_first_hop(served: Deployment) -> None:
        observation = selector_answer_observation(routing.routing_context)
        if observation is not None:
            observation.answered(
                served.route_group_key or served.model_name,
                served.deployment_id,
                streaming=True,
            )
        update_served_route_decision(
            request,
            primary_deployment_id=routing.primary_deployment.deployment_id,
            served_deployment_id=served.deployment_id,
        )

    return await session.open(
        on_first_hop=on_first_hop,
        headers=lambda: route_decision_headers(request),
    )
