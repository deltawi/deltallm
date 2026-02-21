from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from src.billing.cost import compute_cost
from src.callbacks import CallbackManager, build_standard_logging_payload
from src.middleware.auth import require_api_key
from src.middleware.rate_limit import enforce_rate_limits
from src.metrics import (
    increment_request,
    increment_request_failure,
    increment_spend,
    infer_provider,
    observe_api_latency,
    observe_request_latency,
)
from src.models.errors import InvalidRequestError, PermissionDeniedError
from src.models.requests import AudioSpeechRequest
from src.router.router import Deployment

router = APIRouter(prefix="/v1", tags=["audio"])


AUDIO_CONTENT_TYPES = {
    "mp3": "audio/mpeg",
    "opus": "audio/opus",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "pcm": "audio/pcm",
}


async def _execute_tts(
    request: Request,
    payload: AudioSpeechRequest,
    deployment: Deployment,
) -> dict[str, Any]:
    params = deployment.litellm_params
    api_key = params.get("api_key")
    if not api_key:
        raise InvalidRequestError(message="Provider API key is missing for selected model")

    api_base = params.get("api_base", request.app.state.settings.openai_base_url).rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    upstream_payload = payload.model_dump(exclude_unset=True)
    upstream_model = params.get("model")
    if upstream_model and "/" in upstream_model:
        upstream_payload["model"] = upstream_model.split("/", 1)[1]

    from src.routers.utils import apply_default_params
    apply_default_params(upstream_payload, deployment.model_info)

    upstream_start = perf_counter()
    response = await request.app.state.http_client.post(
        f"{api_base}/audio/speech",
        headers=headers,
        json=upstream_payload,
        timeout=params.get("timeout") or 300,
    )
    if response.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"Upstream TTS call failed with status {response.status_code}",
            request=httpx.Request("POST", f"{api_base}/audio/speech"),
            response=response,
        )
    return {
        "_audio_bytes": response.content,
        "_api_latency_ms": (perf_counter() - upstream_start) * 1000,
        "_api_base": api_base,
        "_deployment_model": params.get("model"),
        "_model_info": deployment.model_info,
        "_response_format": upstream_payload.get("response_format") or payload.response_format or "mp3",
    }


@router.post("/audio/speech", dependencies=[Depends(require_api_key), Depends(enforce_rate_limits)])
async def audio_speech(request: Request, payload: AudioSpeechRequest):
    request_start = perf_counter()
    callback_start = datetime.now(tz=UTC)
    auth = request.state.user_api_key
    if auth.models and payload.model not in auth.models:
        raise PermissionDeniedError(message=f"Model '{payload.model}' is not allowed for this key")

    callback_manager: CallbackManager = getattr(request.app.state, "callback_manager", CallbackManager())
    request_data = payload.model_dump(exclude_none=True)

    app_router = request.app.state.router
    model_group = app_router.resolve_model_group(payload.model)
    request_context = {"metadata": {}, "user_id": auth.user_id or auth.api_key}
    primary = app_router.require_deployment(
        model_group=model_group,
        deployment=await app_router.select_deployment(model_group, request_context),
    )
    api_provider = infer_provider(primary.litellm_params.get("model"))
    request_id = request.headers.get("x-request-id")

    try:
        result = await request.app.state.failover_manager.execute_with_failover(
            primary_deployment=primary,
            model_group=model_group,
            execute=lambda dep: _execute_tts(request, payload, dep),
        )
        await request.app.state.passive_health_tracker.record_request_outcome(primary.deployment_id, success=True)

        audio_bytes = result["_audio_bytes"]
        api_latency_ms = result["_api_latency_ms"]
        api_base = result["_api_base"]
        deployment_model = result["_deployment_model"]
        model_info = result["_model_info"]
        effective_format = result["_response_format"]

        input_chars = len(payload.input)
        usage = {"characters": input_chars}
        request_cost = compute_cost(mode="audio_speech", usage=usage, model_info=model_info)
        increment_request(
            model=payload.model, api_provider=api_provider,
            api_key=auth.api_key, user=auth.user_id, team=auth.team_id, status_code=200,
        )
        increment_spend(
            model=payload.model, api_provider=api_provider,
            api_key=auth.api_key, user=auth.user_id, team=auth.team_id, spend=request_cost,
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
                call_type="audio_speech",
                usage=usage,
                cost=request_cost,
                metadata={"api_base": api_base},
                cache_hit=False,
                start_time=callback_start,
                end_time=datetime.now(tz=UTC),
            )
        except Exception:
            pass
        observe_request_latency(model=payload.model, api_provider=api_provider, status_code=200, latency_seconds=perf_counter() - request_start)
        observe_api_latency(model=payload.model, api_provider=api_provider, latency_seconds=api_latency_ms / 1000)
        callback_payload = build_standard_logging_payload(
            call_type="audio_speech", request_id=request_id, model=payload.model,
            deployment_model=deployment_model, request_payload=request_data, response_obj={"bytes": len(audio_bytes)},
            user_api_key_dict=auth.model_dump(mode="json"), start_time=callback_start, end_time=datetime.now(tz=UTC),
            api_base=api_base, response_cost=request_cost, api_latency_ms=api_latency_ms,
            turn_off_message_logging=bool(getattr(request.app.state, "turn_off_message_logging", False)),
        )
        callback_manager.dispatch_success_callbacks(callback_payload)
        content_type = AUDIO_CONTENT_TYPES.get(effective_format, "audio/mpeg")
        return Response(content=audio_bytes, media_type=content_type)
    except httpx.HTTPError as exc:
        await request.app.state.passive_health_tracker.record_request_outcome(primary.deployment_id, success=False, error=str(exc))
        status_code = getattr(getattr(exc, "response", None), "status_code", 502)
        increment_request(model=payload.model, api_provider=api_provider, api_key=auth.api_key, user=auth.user_id, team=auth.team_id, status_code=status_code)
        increment_request_failure(model=payload.model, api_provider=api_provider, error_type=exc.__class__.__name__)
        observe_request_latency(model=payload.model, api_provider=api_provider, status_code=status_code, latency_seconds=perf_counter() - request_start)
        raise InvalidRequestError(message=f"Audio speech request failed: {exc}") from exc
    except Exception as exc:
        await request.app.state.passive_health_tracker.record_request_outcome(primary.deployment_id, success=False, error=str(exc))
        raise
