from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

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
from src.router.router import Deployment

router = APIRouter(prefix="/v1", tags=["audio"])


async def _execute_stt(
    request: Request,
    file_content: bytes,
    filename: str,
    content_type: str,
    model: str,
    language: str | None,
    prompt: str | None,
    response_format: str | None,
    temperature: float | None,
    deployment: Deployment,
) -> dict[str, Any]:
    params = deployment.litellm_params
    api_key = params.get("api_key")
    if not api_key:
        raise InvalidRequestError(message="Provider API key is missing for selected model")

    api_base = params.get("api_base", request.app.state.settings.openai_base_url).rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}

    upstream_model = params.get("model")
    form_model = upstream_model.split("/", 1)[1] if upstream_model and "/" in upstream_model else model

    from src.routers.utils import apply_default_params
    _stt_defaults: dict[str, Any] = {"model": form_model}
    if language:
        _stt_defaults["language"] = language
    if prompt:
        _stt_defaults["prompt"] = prompt
    if response_format:
        _stt_defaults["response_format"] = response_format
    if temperature is not None:
        _stt_defaults["temperature"] = str(temperature)
    apply_default_params(_stt_defaults, deployment.model_info)

    form_data: dict[str, str] = {}
    for _fk, _fv in _stt_defaults.items():
        form_data[_fk] = str(_fv) if _fv is not None else ""

    upstream_start = perf_counter()
    response = await request.app.state.http_client.post(
        f"{api_base}/audio/transcriptions",
        headers=headers,
        files={"file": (filename, file_content, content_type)},
        data=form_data,
        timeout=params.get("timeout") or 600,
    )
    if response.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"Upstream transcription failed with status {response.status_code}",
            request=httpx.Request("POST", f"{api_base}/audio/transcriptions"),
            response=response,
        )
    data = response.json() if "json" in (response_format or "json") else {"text": response.text}
    data["_api_latency_ms"] = (perf_counter() - upstream_start) * 1000
    data["_api_base"] = api_base
    data["_deployment_model"] = params.get("model")
    data["_model_info"] = deployment.model_info
    return data


@router.post("/audio/transcriptions", dependencies=[Depends(require_api_key), Depends(enforce_rate_limits)])
async def audio_transcriptions(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form(...),
    language: str | None = Form(default=None),
    prompt: str | None = Form(default=None),
    response_format: str | None = Form(default="json"),
    temperature: float | None = Form(default=0),
):
    request_start = perf_counter()
    callback_start = datetime.now(tz=UTC)
    auth = request.state.user_api_key
    if auth.models and model not in auth.models:
        raise PermissionDeniedError(message=f"Model '{model}' is not allowed for this key")

    callback_manager: CallbackManager = getattr(request.app.state, "callback_manager", CallbackManager())
    request_data = {"model": model, "language": language, "prompt": prompt, "response_format": response_format, "temperature": temperature}

    file_content = await file.read()
    filename = file.filename or "audio.wav"
    content_type_str = file.content_type or "application/octet-stream"

    app_router = request.app.state.router
    model_group = app_router.resolve_model_group(model)
    request_context = {"metadata": {}, "user_id": auth.user_id or auth.api_key}
    primary = app_router.require_deployment(
        model_group=model_group,
        deployment=await app_router.select_deployment(model_group, request_context),
    )
    api_provider = infer_provider(primary.litellm_params.get("model"))
    request_id = request.headers.get("x-request-id")

    try:
        data = await request.app.state.failover_manager.execute_with_failover(
            primary_deployment=primary,
            model_group=model_group,
            execute=lambda dep: _execute_stt(
                request, file_content, filename, content_type_str,
                model, language, prompt, response_format, temperature, dep,
            ),
        )
        await request.app.state.passive_health_tracker.record_request_outcome(primary.deployment_id, success=True)

        api_latency_ms = data.pop("_api_latency_ms", 0)
        api_base = data.pop("_api_base", "")
        deployment_model = data.pop("_deployment_model", None)
        model_info = data.pop("_model_info", {})

        duration_seconds = data.get("duration", 0)
        usage = {"duration_seconds": duration_seconds, "file_size_bytes": len(file_content)}
        request_cost = compute_cost(mode="audio_transcription", usage=usage, model_info=model_info)
        increment_request(
            model=model, api_provider=api_provider,
            api_key=auth.api_key, user=auth.user_id, team=auth.team_id, status_code=200,
        )
        increment_spend(
            model=model, api_provider=api_provider,
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
                model=model,
                call_type="audio_transcription",
                usage=usage,
                cost=request_cost,
                metadata={"api_base": api_base},
                cache_hit=False,
                start_time=callback_start,
                end_time=datetime.now(tz=UTC),
            )
        except Exception:
            pass
        observe_request_latency(model=model, api_provider=api_provider, status_code=200, latency_seconds=perf_counter() - request_start)
        observe_api_latency(model=model, api_provider=api_provider, latency_seconds=api_latency_ms / 1000)
        callback_payload = build_standard_logging_payload(
            call_type="audio_transcription", request_id=request_id, model=model,
            deployment_model=deployment_model, request_payload=request_data, response_obj=data,
            user_api_key_dict=auth.model_dump(mode="json"), start_time=callback_start, end_time=datetime.now(tz=UTC),
            api_base=api_base, response_cost=request_cost, api_latency_ms=api_latency_ms,
            turn_off_message_logging=bool(getattr(request.app.state, "turn_off_message_logging", False)),
        )
        callback_manager.dispatch_success_callbacks(callback_payload)
        return JSONResponse(status_code=200, content=data)
    except httpx.HTTPError as exc:
        await request.app.state.passive_health_tracker.record_request_outcome(primary.deployment_id, success=False, error=str(exc))
        status_code = getattr(getattr(exc, "response", None), "status_code", 502)
        increment_request(model=model, api_provider=api_provider, api_key=auth.api_key, user=auth.user_id, team=auth.team_id, status_code=status_code)
        increment_request_failure(model=model, api_provider=api_provider, error_type=exc.__class__.__name__)
        observe_request_latency(model=model, api_provider=api_provider, status_code=status_code, latency_seconds=perf_counter() - request_start)
        raise InvalidRequestError(message=f"Audio transcription request failed: {exc}") from exc
    except Exception as exc:
        await request.app.state.passive_health_tracker.record_request_outcome(primary.deployment_id, success=False, error=str(exc))
        raise
