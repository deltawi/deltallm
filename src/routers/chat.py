from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

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
from src.models.errors import InvalidRequestError, PermissionDeniedError
from src.models.requests import ChatCompletionRequest
from src.providers.openai import OpenAIAdapter
from src.router.router import Deployment

router = APIRouter(prefix="/v1", tags=["chat"])


async def _execute_chat(
    request: Request,
    payload: ChatCompletionRequest,
    deployment: Deployment,
) -> tuple[dict[str, Any], float]:
    params = deployment.litellm_params
    api_key = params.get("api_key")
    if not api_key:
        raise InvalidRequestError(message="Provider API key is missing for selected model")

    adapter: OpenAIAdapter = request.app.state.openai_adapter
    upstream_payload = await adapter.translate_request(payload, params)
    api_base = params.get("api_base", request.app.state.settings.openai_base_url).rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    from src.routers.utils import apply_default_params
    apply_default_params(upstream_payload, deployment.model_info)

    await request.app.state.router_state_backend.increment_active(deployment.deployment_id)
    upstream_start = perf_counter()
    try:
        response = await request.app.state.http_client.post(
            f"{api_base}/chat/completions",
            headers=headers,
            json=upstream_payload,
            timeout=params.get("timeout") or 300,
        )
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"Upstream chat call failed with status {response.status_code}",
                request=httpx.Request("POST", f"{api_base}/chat/completions"),
                response=response,
            )
        data = response.json()
        canonical = await adapter.translate_response(data, payload.model)

        total_tokens = int((data.get("usage") or {}).get("total_tokens") or 0)
        await request.app.state.router_state_backend.increment_usage(deployment.deployment_id, total_tokens)
        return canonical.model_dump(mode="json"), (perf_counter() - upstream_start) * 1000
    finally:
        await request.app.state.router_state_backend.decrement_active(deployment.deployment_id)
        await request.app.state.router_state_backend.record_latency(
            deployment.deployment_id,
            (perf_counter() - upstream_start) * 1000,
        )


@router.post("/chat/completions", dependencies=[Depends(require_api_key), Depends(enforce_rate_limits)])
async def chat_completions(request: Request, payload: ChatCompletionRequest):
    request_start = perf_counter()
    callback_start = datetime.now(tz=UTC)
    auth = request.state.user_api_key
    if auth.models and payload.model not in auth.models:
        raise PermissionDeniedError(message=f"Model '{payload.model}' is not allowed for this key")

    guardrail_middleware = request.app.state.guardrail_middleware
    callback_manager: CallbackManager = getattr(request.app.state, "callback_manager", CallbackManager())
    request_data = payload.model_dump(exclude_none=True)
    request_data = await callback_manager.execute_pre_call_hooks(
        user_api_key_dict=auth.model_dump(mode="json"),
        cache=getattr(request.state, "cache_context", None),
        data=request_data,
        call_type="completion",
    )
    request_data = await guardrail_middleware.run_pre_call(
        request_data=request_data,
        user_api_key_dict=auth.model_dump(mode="python"),
        call_type="completion",
    )
    payload = ChatCompletionRequest.model_validate(request_data)

    router = request.app.state.router
    model_group = router.resolve_model_group(payload.model)
    request_context = {"metadata": payload.metadata or {}, "user_id": auth.user_id or auth.api_key}
    primary = router.require_deployment(
        model_group=model_group,
        deployment=await router.select_deployment(model_group, request_context),
    )
    api_provider = infer_provider(primary.litellm_params.get("model"))
    request_id = request.headers.get("x-request-id")
    api_base = primary.litellm_params.get("api_base", request.app.state.settings.openai_base_url).rstrip("/")
    cache_context = getattr(request.state, "cache_context", None)
    cache_hit = bool(getattr(cache_context, "hit", False)) if cache_context is not None else False
    cache_key = getattr(cache_context, "cache_key", None) if cache_context is not None else None

    try:
        if payload.stream:
            params = primary.litellm_params
            api_key = params.get("api_key")
            if not api_key:
                raise InvalidRequestError(message="Provider API key is missing for selected model")

            adapter: OpenAIAdapter = request.app.state.openai_adapter
            upstream_payload = await adapter.translate_request(payload, params)
            api_base = params.get("api_base", request.app.state.settings.openai_base_url).rstrip("/")
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

            async def stream_sse():
                cache_context = getattr(request.state, "cache_context", None)
                stream_handler = getattr(request.app.state, "streaming_cache_handler", None)
                stream_id = None
                stream_write_context: StreamWriteContext | None = None
                if (
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

                async with request.app.state.http_client.stream(
                    "POST",
                    f"{api_base}/chat/completions",
                    headers=headers,
                    json=upstream_payload,
                    timeout=params.get("timeout") or 300,
                ) as response:
                    response.raise_for_status()
                    try:
                        async for line in response.aiter_lines():
                            if not line:
                                continue

                            if stream_id is not None and stream_handler is not None:
                                stream_handler.add_chunk_from_line(stream_id, line)
                                if line.strip() == "data: [DONE]" and stream_write_context is not None:
                                    await stream_handler.finalize_and_store(stream_id, stream_write_context)

                            yield f"{line}\n\n"
                        callback_payload = build_standard_logging_payload(
                            call_type="completion",
                            request_id=request_id,
                            model=payload.model,
                            deployment_model=params.get("model"),
                            request_payload=request_data,
                            response_obj={"object": "chat.completion.chunk"},
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
                            response={"object": "chat.completion.chunk"},
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
                            api_base=api_base,
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
                        raise

            return StreamingResponse(
                stream_sse(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "x-litellm-cache-hit": "false",
                },
            )

        payload_data, api_latency_ms = await request.app.state.failover_manager.execute_with_failover(
            primary_deployment=primary,
            model_group=model_group,
            execute=lambda deployment: _execute_chat(request, payload, deployment),
        )
        await request.app.state.passive_health_tracker.record_request_outcome(primary.deployment_id, success=True)
        usage = payload_data.get("usage") or {}
        _deploy_pricing = None
        if primary.input_cost_per_token or primary.output_cost_per_token:
            from src.billing.cost import ModelPricing
            _deploy_pricing = ModelPricing(
                input_cost_per_token=primary.input_cost_per_token,
                output_cost_per_token=primary.output_cost_per_token,
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
        try:
            await request.app.state.spend_tracking_service.log_spend(
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
        except Exception:
            pass
        observe_request_latency(
            model=payload.model,
            api_provider=api_provider,
            status_code=200,
            latency_seconds=perf_counter() - request_start,
        )
        observe_api_latency(model=payload.model, api_provider=api_provider, latency_seconds=api_latency_ms / 1000)
        callback_payload = build_standard_logging_payload(
            call_type="completion",
            request_id=request_id,
            model=payload.model,
            deployment_model=primary.litellm_params.get("model"),
            request_payload=request_data,
            response_obj=payload_data,
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
            response=payload_data,
        )
        await guardrail_middleware.run_post_call_success(
            request_data=request_data,
            user_api_key_dict=auth.model_dump(mode="python"),
            response_data=payload_data,
            call_type="completion",
        )
        return JSONResponse(status_code=200, content=payload_data)
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
            deployment_model=primary.litellm_params.get("model"),
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
            deployment_model=primary.litellm_params.get("model"),
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
        raise
