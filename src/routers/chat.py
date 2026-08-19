from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.cache.pricing import cache_pricing_snapshot_from_deployment
from src.cache.streaming import StreamWriteContext
from src.chat import (
    audit_action_for_path,
    emit_nonstream_failure,
    emit_nonstream_success,
    emit_stream_failure,
    emit_stream_success,
    execute_chat,
    open_stream_with_first_chunk,
    run_text_preflight,
)
from src.chat.stream_usage import StreamUsageTracker
from src.middleware.auth import require_api_key
from src.mcp.orchestrator import MCPChatOrchestrator, chat_request_has_mcp_tools
from src.models.errors import InvalidRequestError, ServiceUnavailableError
from src.models.requests import ChatCompletionRequest
from src.providers.registry import resolve_chat_upstream
from src.providers.resolution import resolve_provider
from src.router import ROUTING_MODE_CONTEXT_KEY
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
    auth, payload, request_data, callback_manager, guardrail_middleware = await run_text_preflight(
        request=request,
        payload=payload,
        request_data=request_data,
    )
    has_mcp_tools = chat_request_has_mcp_tools(payload)

    router = request.app.state.router
    model_group = router.resolve_model_group(payload.model)
    request_context = {
        "metadata": payload.metadata or {},
        "user_id": auth.user_id or auth.api_key,
        ROUTING_MODE_CONTEXT_KEY: "chat",
    }
    primary = router.require_deployment(
        model_group=model_group,
        deployment=await router.select_deployment(model_group, request_context),
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

            managed_stream = await request.app.state.failover_manager.execute_managed_with_failover(
                primary_deployment=primary,
                model_group=model_group,
                execute=lambda dep: open_stream_with_first_chunk(request, payload, dep),
                on_attempt=track_attempt,
                routing_context=request_context,
                **failover_kwargs,
            )
            opened_stream = managed_stream.value
            served_deployment = managed_stream.deployment
            update_served_route_decision(
                request,
                primary_deployment_id=primary.deployment_id,
                served_deployment_id=served_deployment.deployment_id,
            )

            async def stream_sse():
                cache_context = getattr(request.state, "cache_context", None)
                stream_handler = getattr(request.app.state, "streaming_cache_handler", None)
                stream_id = None
                stream_write_context: StreamWriteContext | None = None
                stream_usage = StreamUsageTracker()
                failure_exc: BaseException | None = None
                stream_cache_complete = False
                try:
                    params = opened_stream.params
                    api_base_local = opened_stream.api_base
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

                    async with asyncio.timeout(managed_stream.deadline.require_remaining()):
                        initial = opened_stream.first_line
                        if initial:
                            line_info = stream_usage.add_line(initial)
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
                        async for line in opened_stream.translated_stream:
                            if not line:
                                continue

                            line_info = stream_usage.add_line(line)
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
                        api_base=api_base_local,
                        params=params,
                        usage=resolved_usage.usage,
                        usage_metadata=resolved_usage.metadata(),
                    )
                except asyncio.CancelledError as exc:
                    failure_exc = exc
                    raise
                except Exception as exc:
                    failure_exc = exc
                    if stream_id is not None and stream_handler is not None:
                        stream_handler.discard_stream(stream_id)
                    failure_params = (
                        opened_stream.params
                        if opened_stream is not None
                        else primary.deltallm_params
                    )
                    failure_api_base = (
                        opened_stream.api_base if opened_stream is not None else api_base
                    )
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
                        exc=exc,
                    )
                    raise
                finally:
                    try:
                        await opened_stream.close(failure_exc)
                    finally:
                        await managed_stream.release()

            return StreamingResponse(
                stream_sse(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "x-deltallm-cache-hit": "false",
                    **route_decision_headers(request),
                },
            )

        async def _execute_for_deployment(deployment):  # noqa: ANN001, ANN202
            if not has_mcp_tools:
                return await execute_chat(request, payload, deployment)
            gateway = getattr(request.app.state, "mcp_gateway_service", None)
            if gateway is None:
                raise ServiceUnavailableError(message="MCP gateway service is not available")
            orchestrator = MCPChatOrchestrator(
                gateway,
                audit_service=getattr(request.app.state, "audit_service", None),
            )
            return await orchestrator.execute(
                request=request,
                auth=auth,
                payload=payload,
                execute_chat_call=lambda request_payload: execute_chat(
                    request, request_payload, deployment
                ),
                guardrail_middleware=guardrail_middleware,
            )

        (
            (payload_data, api_latency_ms),
            served_deployment,
        ) = await request.app.state.failover_manager.execute_with_failover(
            primary_deployment=primary,
            model_group=model_group,
            execute=_execute_for_deployment,
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
        status_code = getattr(getattr(exc, "response", None), "status_code", 502)
        await emit_nonstream_failure(
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
        )
        adapter = request.app.state.openai_adapter
        raise adapter.map_error(exc) from exc
    except Exception as exc:
        status_code = int(getattr(exc, "status_code", 500) or 500)
        await emit_nonstream_failure(
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
        )
        raise
