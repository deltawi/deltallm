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
from src.models.errors import InvalidRequestError, ModelNotFoundError, PermissionDeniedError

router = APIRouter(prefix="/v1", tags=["audio"])


def _pick_deployment(request: Request, model_name: str) -> dict[str, Any]:
    deployments = request.app.state.model_registry.get(model_name) or []
    for d in deployments:
        if d.get("model_info", {}).get("mode") == "audio_transcription":
            return d
    raise ModelNotFoundError(message=f"No audio_transcription deployment found for model '{model_name}'")


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

    deployment = _pick_deployment(request, model)
    params = deployment["litellm_params"]
    deployment_id = str(deployment.get("deployment_id") or f"{model}-0")
    model_info = deployment.get("model_info", {})
    api_provider = infer_provider(params.get("model"))
    api_key = params.get("api_key")
    if not api_key:
        raise InvalidRequestError(message="Provider API key is missing for selected model")

    api_base = params.get("api_base", request.app.state.settings.openai_base_url).rstrip("/")
    request_id = request.headers.get("x-request-id")
    headers = {"Authorization": f"Bearer {api_key}"}

    file_content = await file.read()
    upstream_model = params.get("model")
    form_model = upstream_model.split("/", 1)[1] if upstream_model and "/" in upstream_model else model

    form_data: dict[str, Any] = {"model": (None, form_model)}
    if language:
        form_data["language"] = (None, language)
    if prompt:
        form_data["prompt"] = (None, prompt)
    if response_format:
        form_data["response_format"] = (None, response_format)
    if temperature is not None:
        form_data["temperature"] = (None, str(temperature))

    try:
        upstream_start = perf_counter()
        response = await request.app.state.http_client.post(
            f"{api_base}/audio/transcriptions",
            headers=headers,
            files={"file": (file.filename or "audio.wav", file_content, file.content_type or "application/octet-stream")},
            data={k: v[1] for k, v in form_data.items()},
            timeout=params.get("timeout") or 600,
        )
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"Upstream transcription failed with status {response.status_code}",
                request=httpx.Request("POST", f"{api_base}/audio/transcriptions"),
                response=response,
            )
        data = response.json() if "json" in (response_format or "json") else {"text": response.text}
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
        observe_api_latency(model=model, api_provider=api_provider, latency_seconds=perf_counter() - upstream_start)
        callback_payload = build_standard_logging_payload(
            call_type="audio_transcription", request_id=request_id, model=model,
            deployment_model=params.get("model"), request_payload=request_data, response_obj=data,
            user_api_key_dict=auth.model_dump(mode="json"), start_time=callback_start, end_time=datetime.now(tz=UTC),
            api_base=api_base, response_cost=request_cost, api_latency_ms=(perf_counter() - upstream_start) * 1000,
            turn_off_message_logging=bool(getattr(request.app.state, "turn_off_message_logging", False)),
        )
        callback_manager.dispatch_success_callbacks(callback_payload)
        return JSONResponse(status_code=200, content=data)
    except httpx.HTTPError as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", 502)
        increment_request(model=model, api_provider=api_provider, api_key=auth.api_key, user=auth.user_id, team=auth.team_id, status_code=status_code)
        increment_request_failure(model=model, api_provider=api_provider, error_type=exc.__class__.__name__)
        observe_request_latency(model=model, api_provider=api_provider, status_code=status_code, latency_seconds=perf_counter() - request_start)
        raise InvalidRequestError(message=f"Audio transcription request failed: {exc}") from exc
