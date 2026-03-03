from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.billing.cost import completion_cost
from src.callbacks import CallbackManager, build_standard_logging_payload
from src.cache.streaming import StreamWriteContext
from src.middleware.auth import require_api_key
from src.middleware.rate_limit import enforce_rate_limits
from src.metrics import (
    increment_request,
    increment_request_failure,
    increment_spend,
    increment_usage,
    infer_provider,
    observe_api_latency,
    observe_request_latency,
)
from src.models.errors import InvalidRequestError, PermissionDeniedError, ServiceUnavailableError
from src.models.requests import ChatCompletionRequest
from src.providers.base import ProviderAdapter
from src.router.router import Deployment
from src.services.audit_service import AuditEventInput, AuditPayloadInput, AuditService

router = APIRouter(prefix="/v1", tags=["chat"])


def _audit_action_for_path(path: str) -> str:
    if path == "/v1/chat/completions":
        return "CHAT_COMPLETION_REQUEST"
    if path == "/v1/completions":
        return "COMPLETION_REQUEST"
    if path == "/v1/responses":
        return "RESPONSES_REQUEST"
    return "TEXT_GENERATION_REQUEST"


def _request_client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_hop = forwarded_for.split(",", 1)[0].strip()
        if first_hop:
            return first_hop
    if request.client and request.client.host:
        return request.client.host
    return None


def _emit_text_audit_event(
    *,
    request: Request,
    auth: Any,
    action: str,
    model: str,
    status: str,
    request_start: float,
    request_data: dict[str, Any] | None,
    response_data: dict[str, Any] | None,
    error: Exception | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
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
            action=action,
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
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            error_type=error.__class__.__name__ if error is not None else None,
            error_code=getattr(getattr(error, "response", None), "status_code", None) if error is not None else None,
            metadata=metadata or {},
        ),
        payloads=payloads,
        critical=True,
    )


@dataclass
class _OpenedStream:
    context_manager: Any
    response: Any
    translated_stream: Any
    first_line: str
    deployment: Deployment
    params: dict[str, Any]
    api_base: str

    async def close(self, exc: Exception | None = None) -> None:
        if exc is None:
            await self.context_manager.__aexit__(None, None, None)
        else:
            await self.context_manager.__aexit__(type(exc), exc, exc.__traceback__)


def _resolve_chat_upstream(
    request: Request,
    params: dict[str, Any],
) -> tuple[ProviderAdapter, str, str, dict[str, str], int]:
    api_key = params.get("api_key")
    if not api_key:
        raise InvalidRequestError(message="Provider API key is missing for selected model")

    provider = infer_provider(params.get("model"))
    timeout = int(params.get("timeout") or 300)
    if provider == "anthropic":
        adapter: ProviderAdapter = request.app.state.anthropic_adapter
        api_base = str(params.get("api_base") or "https://api.anthropic.com/v1").rstrip("/")
        endpoint = "/messages"
        headers = {
            "x-api-key": str(api_key),
            "anthropic-version": str(params.get("api_version") or "2023-06-01"),
            "Content-Type": "application/json",
        }
        return adapter, api_base, endpoint, headers, timeout

    adapter = request.app.state.openai_adapter
    api_base = str(params.get("api_base", request.app.state.settings.openai_base_url)).rstrip("/")
    endpoint = "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    return adapter, api_base, endpoint, headers, timeout


async def _execute_chat(
    request: Request,
    payload: ChatCompletionRequest,
    deployment: Deployment,
) -> tuple[dict[str, Any], float]:
    params = deployment.deltallm_params
    adapter, api_base, endpoint, headers, timeout = _resolve_chat_upstream(request, params)
    upstream_payload = await adapter.translate_request(payload, params)

    from src.routers.utils import apply_default_params
    apply_default_params(upstream_payload, deployment.model_info)

    await request.app.state.router_state_backend.increment_active(deployment.deployment_id)
    upstream_start = perf_counter()
    try:
        response = await request.app.state.http_client.post(
            f"{api_base}{endpoint}",
            headers=headers,
            json=upstream_payload,
            timeout=timeout,
        )
        if response.status_code >= 400:
            status_exc = httpx.HTTPStatusError(
                f"Upstream chat call failed with status {response.status_code}",
                request=httpx.Request("POST", f"{api_base}{endpoint}"),
                response=response,
            )
            raise adapter.map_error(status_exc)
        data = response.json()
        canonical = await adapter.translate_response(data, payload.model)
        canonical_payload = canonical.model_dump(mode="json")

        total_tokens = int((canonical_payload.get("usage") or {}).get("total_tokens") or 0)
        await request.app.state.router_state_backend.increment_usage(deployment.deployment_id, total_tokens)
        return canonical_payload, (perf_counter() - upstream_start) * 1000
    finally:
        await request.app.state.router_state_backend.decrement_active(deployment.deployment_id)
        await request.app.state.router_state_backend.record_latency(
            deployment.deployment_id,
            (perf_counter() - upstream_start) * 1000,
        )


async def _open_stream_with_first_chunk(
    request: Request,
    payload: ChatCompletionRequest,
    deployment: Deployment,
) -> _OpenedStream:
    params = deployment.deltallm_params
    adapter, api_base, endpoint, headers, timeout = _resolve_chat_upstream(request, params)
    upstream_payload = await adapter.translate_request(payload, params)

    from src.routers.utils import apply_default_params
    apply_default_params(upstream_payload, deployment.model_info)

    context_manager = request.app.state.http_client.stream(
        "POST",
        f"{api_base}{endpoint}",
        headers=headers,
        json=upstream_payload,
        timeout=timeout,
    )
    response = await context_manager.__aenter__()
    try:
        if response.status_code >= 400:
            status_exc = httpx.HTTPStatusError(
                f"Upstream chat call failed with status {response.status_code}",
                request=httpx.Request("POST", f"{api_base}{endpoint}"),
                response=response,
            )
            raise adapter.map_error(status_exc)

        translated_stream = adapter.translate_stream(response.aiter_lines())
        first_line: str | None = None
        async for line in translated_stream:
            if line:
                first_line = line
                break
        if first_line is None:
            raise ServiceUnavailableError(message="Provider stream ended before first chunk")

        return _OpenedStream(
            context_manager=context_manager,
            response=response,
            translated_stream=translated_stream,
            first_line=first_line,
            deployment=deployment,
            params=params,
            api_base=api_base,
        )
    except Exception as exc:
        await context_manager.__aexit__(type(exc), exc, exc.__traceback__)
        raise


@router.post("/chat/completions", dependencies=[Depends(require_api_key), Depends(enforce_rate_limits)])
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
    auth, payload, request_data, callback_manager, guardrail_middleware = await _run_text_preflight(
        request=request,
        payload=payload,
        request_data=request_data,
    )

    router = request.app.state.router
    model_group = router.resolve_model_group(payload.model)
    request_context = {"metadata": payload.metadata or {}, "user_id": auth.user_id or auth.api_key}
    primary = router.require_deployment(
        model_group=model_group,
        deployment=await router.select_deployment(model_group, request_context),
    )
    api_provider = infer_provider(primary.deltallm_params.get("model"))
    request_id = request.headers.get("x-request-id")
    api_base = primary.deltallm_params.get("api_base", request.app.state.settings.openai_base_url).rstrip("/")
    cache_context = getattr(request.state, "cache_context", None)
    cache_hit = bool(getattr(cache_context, "hit", False)) if cache_context is not None else False
    cache_key = getattr(cache_context, "cache_key", None) if cache_context is not None else None
    audit_action = _audit_action_for_path(request.url.path)

    try:
        if payload.stream:
            async def stream_sse():
                cache_context = getattr(request.state, "cache_context", None)
                stream_handler = getattr(request.app.state, "streaming_cache_handler", None)
                stream_id = None
                stream_write_context: StreamWriteContext | None = None
                opened_stream = await request.app.state.failover_manager.execute_with_failover(
                    primary_deployment=primary,
                    model_group=model_group,
                    execute=lambda dep: _open_stream_with_first_chunk(request, payload, dep),
                    return_deployment=False,
                )
                params = opened_stream.params
                api_base_local = opened_stream.api_base
                if (
                    enable_stream_cache
                    and request.url.path == "/v1/chat/completions"
                    and
                    cache_context is not None
                    and stream_handler is not None
                    and cache_context.options.control.value != "no-store"
                ):
                    cache_ttl = int(
                        cache_context.options.ttl
                        or getattr(
                            getattr(getattr(request.app.state, "app_config", None), "general_settings", None),
                            "cache_ttl",
                            3600,
                        )
                    )
                    stream_id = cache_context.cache_key
                    stream_write_context = StreamWriteContext(
                        cache_key=cache_context.cache_key,
                        ttl=cache_ttl,
                        model=payload.model,
                    )
                    stream_handler.start_stream(stream_id)

                try:
                    try:
                        initial = opened_stream.first_line
                        if initial:
                            if stream_id is not None and stream_handler is not None:
                                stream_handler.add_chunk_from_line(stream_id, initial)
                            out_line = stream_line_transform(initial) if stream_line_transform is not None else initial
                            if out_line is not None:
                                yield f"{out_line}\n\n"
                        async for line in opened_stream.translated_stream:
                            if not line:
                                continue

                            if stream_id is not None and stream_handler is not None:
                                stream_handler.add_chunk_from_line(stream_id, line)
                                if line.strip() == "data: [DONE]" and stream_write_context is not None:
                                    await stream_handler.finalize_and_store(stream_id, stream_write_context)

                            out_line = stream_line_transform(line) if stream_line_transform is not None else line
                            if out_line is None:
                                continue
                            yield f"{out_line}\n\n"
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
                            api_base=api_base_local,
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
                        _emit_text_audit_event(
                            request=request,
                            auth=auth,
                            action=audit_action,
                            model=payload.model,
                            status="success",
                            request_start=request_start,
                            request_data=request_data,
                            response_data={"object": stream_response_object},
                            metadata={
                                "route": request.url.path,
                                "stream": True,
                                "cache_hit": cache_hit,
                                "cache_key": cache_key,
                                "api_base": api_base_local,
                                "provider": infer_provider(params.get("model")),
                                "deployment_model": params.get("model"),
                            },
                        )
                    except Exception:
                        if stream_id is not None and stream_handler is not None:
                            stream_handler.discard_stream(stream_id)
                        callback_end = datetime.now(tz=UTC)
                        callback_payload = build_standard_logging_payload(
                            call_type="completion",
                            request_id=request_id,
                            model=payload.model,
                            deployment_model=params.get("model"),
                            request_payload=request_data,
                            response_obj=None,
                            user_api_key_dict=auth.model_dump(mode="json"),
                            start_time=callback_start,
                            end_time=callback_end,
                            api_base=api_base_local,
                            cache_hit=cache_hit,
                            cache_key=cache_key,
                            error_info={"error_type": "stream_error"},
                            turn_off_message_logging=bool(getattr(request.app.state, "turn_off_message_logging", False)),
                        )
                        stream_exc = Exception("stream interrupted")
                        callback_manager.dispatch_failure_callbacks(callback_payload, stream_exc)
                        await callback_manager.execute_post_call_failure_hooks(
                            request_data=request_data,
                            original_exception=stream_exc,
                            user_api_key_dict=auth.model_dump(mode="json"),
                        )
                        await guardrail_middleware.run_post_call_failure(
                            request_data=request_data,
                            user_api_key_dict=auth.model_dump(mode="python"),
                            original_exception=stream_exc,
                            call_type="completion",
                        )
                        _emit_text_audit_event(
                            request=request,
                            auth=auth,
                            action=audit_action,
                            model=payload.model,
                            status="error",
                            request_start=request_start,
                            request_data=request_data,
                            response_data=None,
                            error=stream_exc,
                            metadata={
                                "route": request.url.path,
                                "stream": True,
                                "cache_hit": cache_hit,
                                "cache_key": cache_key,
                                "api_base": api_base_local,
                                "provider": infer_provider(params.get("model")),
                                "deployment_model": params.get("model"),
                            },
                        )
                        raise
                finally:
                    await opened_stream.close()

            return StreamingResponse(
                stream_sse(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "x-deltallm-cache-hit": "false",
                },
            )

        (payload_data, api_latency_ms), served_deployment = await request.app.state.failover_manager.execute_with_failover(
            primary_deployment=primary,
            model_group=model_group,
            execute=lambda deployment: _execute_chat(request, payload, deployment),
            return_deployment=True,
        )
        response_payload = response_transform(payload_data) if response_transform is not None else payload_data
        await request.app.state.passive_health_tracker.record_request_outcome(served_deployment.deployment_id, success=True)
        api_provider = infer_provider(served_deployment.deltallm_params.get("model"))
        api_base = str(served_deployment.deltallm_params.get("api_base", request.app.state.settings.openai_base_url)).rstrip("/")
        usage = payload_data.get("usage") or {}
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
        from src.routers.utils import fire_and_forget
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
                metadata={"api_base": api_base},
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
        _emit_text_audit_event(
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
            metadata={
                "route": request.url.path,
                "stream": False,
                "cache_hit": cache_hit,
                "cache_key": cache_key,
                "api_base": api_base,
                "provider": api_provider,
                "deployment_model": served_deployment.deltallm_params.get("model"),
            },
        )
        return JSONResponse(status_code=200, content=response_payload)
    except httpx.HTTPError as exc:
        await guardrail_middleware.run_post_call_failure(
            request_data=request_data,
            user_api_key_dict=auth.model_dump(mode="python"),
            original_exception=exc,
            call_type="completion",
        )
        await request.app.state.passive_health_tracker.record_request_outcome(
            primary.deployment_id,
            success=False,
            error=str(exc),
        )
        status_code = getattr(getattr(exc, "response", None), "status_code", 502)
        increment_request(
            model=payload.model,
            api_provider=api_provider,
            api_key=auth.api_key,
            user=auth.user_id,
            team=auth.team_id,
            status_code=status_code,
        )
        increment_request_failure(
            model=payload.model,
            api_provider=api_provider,
            error_type=exc.__class__.__name__,
        )
        observe_request_latency(
            model=payload.model,
            api_provider=api_provider,
            status_code=status_code,
            latency_seconds=perf_counter() - request_start,
        )
        callback_payload = build_standard_logging_payload(
            call_type="completion",
            request_id=request_id,
            model=payload.model,
            deployment_model=primary.deltallm_params.get("model"),
            request_payload=request_data,
            response_obj=None,
            user_api_key_dict=auth.model_dump(mode="json"),
            start_time=callback_start,
            end_time=datetime.now(tz=UTC),
            api_base=api_base,
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
        _emit_text_audit_event(
            request=request,
            auth=auth,
            action=audit_action,
            model=payload.model,
            status="error",
            request_start=request_start,
            request_data=request_data,
            response_data=None,
            error=exc,
            metadata={
                "route": request.url.path,
                "stream": False,
                "cache_hit": cache_hit,
                "cache_key": cache_key,
                "api_base": api_base,
                "provider": api_provider,
                "deployment_model": primary.deltallm_params.get("model"),
            },
        )
        adapter: OpenAIAdapter = request.app.state.openai_adapter
        raise adapter.map_error(exc) from exc
    except Exception as exc:
        await guardrail_middleware.run_post_call_failure(
            request_data=request_data,
            user_api_key_dict=auth.model_dump(mode="python"),
            original_exception=exc,
            call_type="completion",
        )
        await request.app.state.passive_health_tracker.record_request_outcome(
            primary.deployment_id,
            success=False,
            error=str(exc),
        )
        increment_request(
            model=payload.model,
            api_provider=api_provider,
            api_key=auth.api_key,
            user=auth.user_id,
            team=auth.team_id,
            status_code=500,
        )
        increment_request_failure(
            model=payload.model,
            api_provider=api_provider,
            error_type=exc.__class__.__name__,
        )
        observe_request_latency(
            model=payload.model,
            api_provider=api_provider,
            status_code=500,
            latency_seconds=perf_counter() - request_start,
        )
        callback_payload = build_standard_logging_payload(
            call_type="completion",
            request_id=request_id,
            model=payload.model,
            deployment_model=primary.deltallm_params.get("model"),
            request_payload=request_data,
            response_obj=None,
            user_api_key_dict=auth.model_dump(mode="json"),
            start_time=callback_start,
            end_time=datetime.now(tz=UTC),
            api_base=api_base,
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
        _emit_text_audit_event(
            request=request,
            auth=auth,
            action=audit_action,
            model=payload.model,
            status="error",
            request_start=request_start,
            request_data=request_data,
            response_data=None,
            error=exc,
            metadata={
                "route": request.url.path,
                "stream": False,
                "cache_hit": cache_hit,
                "cache_key": cache_key,
                "api_base": api_base,
                "provider": api_provider,
                "deployment_model": primary.deltallm_params.get("model"),
            },
        )
        raise


async def _run_text_preflight(
    *,
    request: Request,
    payload: ChatCompletionRequest,
    request_data: dict[str, Any] | None,
) -> tuple[Any, ChatCompletionRequest, dict[str, Any], CallbackManager, Any]:
    auth = request.state.user_api_key
    if auth.models and payload.model not in auth.models:
        raise PermissionDeniedError(message=f"Model '{payload.model}' is not allowed for this key")

    from src.routers.utils import enforce_budget_if_configured
    await enforce_budget_if_configured(request, model=payload.model, auth=auth)

    guardrail_middleware = request.app.state.guardrail_middleware
    callback_manager: CallbackManager = getattr(request.app.state, "callback_manager", CallbackManager())
    data = request_data or payload.model_dump(exclude_none=True)
    data = await callback_manager.execute_pre_call_hooks(
        user_api_key_dict=auth.model_dump(mode="json"),
        cache=getattr(request.state, "cache_context", None),
        data=data,
        call_type="completion",
    )
    data = await guardrail_middleware.run_pre_call(
        request_data=data,
        user_api_key_dict=auth.model_dump(mode="python"),
        call_type="completion",
    )
    return auth, ChatCompletionRequest.model_validate(data), data, callback_manager, guardrail_middleware
