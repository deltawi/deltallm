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
    infer_provider,
    observe_api_latency,
    observe_request_latency,
)
from src.models.errors import InvalidRequestError, ModelNotFoundError, PermissionDeniedError
from src.models.requests import EmbeddingRequest

router = APIRouter(prefix="/v1", tags=["embeddings"])


def _pick_deployment(request: Request, model_name: str) -> dict[str, Any]:
    deployments = request.app.state.model_registry.get(model_name) or []
    if not deployments:
        raise ModelNotFoundError(message=f"Model '{model_name}' not found")
    return deployments[0]


@router.post("/embeddings", dependencies=[Depends(require_api_key), Depends(enforce_rate_limits)])
async def embeddings(request: Request, payload: EmbeddingRequest):
    request_start = perf_counter()
    callback_start = datetime.now(tz=UTC)
    auth = request.state.user_api_key
    if auth.models and payload.model not in auth.models:
        raise PermissionDeniedError(message=f"Model '{payload.model}' is not allowed for this key")

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

    deployment = _pick_deployment(request, payload.model)
    params = deployment["litellm_params"]
    deployment_id = str(deployment.get("deployment_id") or f"{payload.model}-0")
    api_provider = infer_provider(params.get("model"))
    api_key = params.get("api_key")
    if not api_key:
        raise InvalidRequestError(message="Provider API key is missing for selected model")

    api_base = params.get("api_base", request.app.state.settings.openai_base_url).rstrip("/")
    request_id = request.headers.get("x-request-id")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    upstream_payload = payload.model_dump(exclude_none=True)
    upstream_model = params.get("model")
    if upstream_model and "/" in upstream_model:
        upstream_payload["model"] = upstream_model.split("/", 1)[1]

    try:
        await request.app.state.router_state_backend.increment_active(deployment_id)
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
        usage = data.get("usage") or {}
        await request.app.state.router_state_backend.increment_usage(
            deployment_id,
            int(usage.get("total_tokens", 0) or 0),
        )
        request_cost = completion_cost(
            model=payload.model,
            usage=usage,
            cache_hit=getattr(request.state, "cache_hit", False),
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
                call_type="embedding",
                usage=usage,
                cost=request_cost,
                metadata={"api_base": api_base},
                cache_hit=getattr(request.state, "cache_hit", False),
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
        observe_api_latency(
            model=payload.model,
            api_provider=api_provider,
            latency_seconds=perf_counter() - upstream_start,
        )
        callback_payload = build_standard_logging_payload(
            call_type="embedding",
            request_id=request_id,
            model=payload.model,
            deployment_model=params.get("model"),
            request_payload=request_data,
            response_obj=data,
            user_api_key_dict=auth.model_dump(mode="json"),
            start_time=callback_start,
            end_time=datetime.now(tz=UTC),
            api_base=api_base,
            response_cost=request_cost,
            api_latency_ms=(perf_counter() - upstream_start) * 1000,
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
        return JSONResponse(status_code=200, content=data)
    except httpx.HTTPError as exc:
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
            call_type="embedding",
            request_id=request_id,
            model=payload.model,
            deployment_model=params.get("model"),
            request_payload=request_data,
            response_obj=None,
            user_api_key_dict=auth.model_dump(mode="json"),
            start_time=callback_start,
            end_time=datetime.now(tz=UTC),
            api_base=api_base,
            error_info={"error_type": exc.__class__.__name__, "message": str(exc)},
            turn_off_message_logging=bool(getattr(request.app.state, "turn_off_message_logging", False)),
        )
        callback_manager.dispatch_failure_callbacks(callback_payload, exc)
        await callback_manager.execute_post_call_failure_hooks(
            request_data=request_data,
            original_exception=exc,
            user_api_key_dict=auth.model_dump(mode="json"),
        )
        await request.app.state.guardrail_middleware.run_post_call_failure(
            request_data=request_data,
            user_api_key_dict=auth.model_dump(mode="python"),
            original_exception=exc,
            call_type="embedding",
        )
        raise InvalidRequestError(message=f"Embedding request failed: {exc}") from exc
    except Exception as exc:
        callback_payload = build_standard_logging_payload(
            call_type="embedding",
            request_id=request_id,
            model=payload.model,
            deployment_model=params.get("model"),
            request_payload=request_data,
            response_obj=None,
            user_api_key_dict=auth.model_dump(mode="json"),
            start_time=callback_start,
            end_time=datetime.now(tz=UTC),
            api_base=api_base,
            error_info={"error_type": exc.__class__.__name__, "message": str(exc)},
            turn_off_message_logging=bool(getattr(request.app.state, "turn_off_message_logging", False)),
        )
        callback_manager.dispatch_failure_callbacks(callback_payload, exc)
        await callback_manager.execute_post_call_failure_hooks(
            request_data=request_data,
            original_exception=exc,
            user_api_key_dict=auth.model_dump(mode="json"),
        )
        await request.app.state.guardrail_middleware.run_post_call_failure(
            request_data=request_data,
            user_api_key_dict=auth.model_dump(mode="python"),
            original_exception=exc,
            call_type="embedding",
        )
        raise
    finally:
        await request.app.state.router_state_backend.decrement_active(deployment_id)
        await request.app.state.router_state_backend.record_latency(
            deployment_id,
            (perf_counter() - request_start) * 1000,
        )
