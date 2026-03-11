from __future__ import annotations

import base64
import json
import wave
from datetime import UTC, datetime
from io import BytesIO
from time import perf_counter
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from src.billing.audio_usage import billing_metadata, normalize_speech_usage
from src.billing.cost import compute_billing_result
from src.callbacks import CallbackManager, build_standard_logging_payload
from src.middleware.auth import require_api_key
from src.middleware.rate_limit import enforce_rate_limits
from src.metrics import (
    increment_request,
    increment_request_failure,
    increment_spend,
    observe_api_latency,
    observe_request_latency,
)
from src.models.errors import InvalidRequestError, PermissionDeniedError
from src.models.requests import AudioSpeechRequest
from src.providers.resolution import resolve_provider, resolve_upstream_model
from src.router.router import Deployment
from src.audit.actions import AuditAction
from src.routers.audit_helpers import emit_audit_event
from src.routers.routing_decision import (
    attach_route_decision,
    capture_initial_route_decision,
    route_failover_kwargs,
    route_decision_headers,
    update_served_route_decision,
)
from src.routers.utils import enforce_budget_if_configured, fire_and_forget

router = APIRouter(prefix="/v1", tags=["audio"])


AUDIO_CONTENT_TYPES = {
    "mp3": "audio/mpeg",
    "opus": "audio/opus",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "pcm": "audio/pcm",
}

SSE_USAGE_PROVIDERS = {"openai", "azure", "azure_openai"}
GEMINI_SUPPORTED_RESPONSE_FORMATS = {"wav", "pcm"}


async def _execute_tts(
    request: Request,
    payload: AudioSpeechRequest,
    deployment: Deployment,
) -> dict[str, Any]:
    params = deployment.deltallm_params
    api_key = params.get("api_key")
    if not api_key:
        raise InvalidRequestError(message="Provider API key is missing for selected model")

    api_base = params.get("api_base", request.app.state.settings.openai_base_url).rstrip("/")
    provider = resolve_provider(params)
    if provider == "gemini":
        return await _execute_gemini_tts(
            request=request,
            payload=payload,
            deployment=deployment,
            api_base=api_base,
            api_key=str(api_key),
        )

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    upstream_payload = payload.model_dump(exclude_unset=True)
    upstream_model = resolve_upstream_model(params)
    if upstream_model:
        upstream_payload["model"] = upstream_model
    if _should_force_tts_sse(model_info=deployment.model_info, provider=provider):
        upstream_payload["stream_format"] = "sse"

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
    audio_bytes = response.content
    billing_payload: dict[str, Any] | None = None
    if upstream_payload.get("stream_format") == "sse":
        audio_bytes, billing_payload = _extract_sse_audio_and_usage(response.content)
    return {
        "_audio_bytes": audio_bytes,
        "_billing_payload": billing_payload,
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
    await enforce_budget_if_configured(request, model=payload.model, auth=auth)

    callback_manager: CallbackManager = getattr(request.app.state, "callback_manager", CallbackManager())
    request_data = payload.model_dump(exclude_none=True)

    app_router = request.app.state.router
    model_group = app_router.resolve_model_group(payload.model)
    request_context = {"metadata": {}, "user_id": auth.user_id or auth.api_key}
    primary = app_router.require_deployment(
        model_group=model_group,
        deployment=await app_router.select_deployment(model_group, request_context),
    )
    failover_kwargs = route_failover_kwargs(request_context)
    capture_initial_route_decision(request, request_context)
    api_provider = resolve_provider(primary.deltallm_params)
    request_id = request.headers.get("x-request-id")

    try:
        result, served_deployment = await request.app.state.failover_manager.execute_with_failover(
            primary_deployment=primary,
            model_group=model_group,
            execute=lambda dep: _execute_tts(request, payload, dep),
            return_deployment=True,
            **failover_kwargs,
        )
        update_served_route_decision(
            request,
            primary_deployment_id=primary.deployment_id,
            served_deployment_id=served_deployment.deployment_id,
        )
        await request.app.state.passive_health_tracker.record_request_outcome(served_deployment.deployment_id, success=True)
        api_provider = resolve_provider(served_deployment.deltallm_params)

        audio_bytes = result["_audio_bytes"]
        api_latency_ms = result["_api_latency_ms"]
        api_base = result["_api_base"]
        deployment_model = result["_deployment_model"]
        model_info = result["_model_info"]
        effective_format = result["_response_format"]
        billing_payload = result.get("_billing_payload")

        usage = normalize_speech_usage(
            request_text=payload.input,
            response_payload=billing_payload,
            provider=api_provider,
        )
        billing = compute_billing_result(mode="audio_speech", usage=usage, model_info=model_info)
        request_cost = billing.cost
        spend_metadata = attach_route_decision(
            {"api_base": api_base, "billing": billing_metadata(billing)},
            request,
        )
        increment_request(
            model=payload.model, api_provider=api_provider,
            api_key=auth.api_key, user=auth.user_id, team=auth.team_id, status_code=200,
        )
        increment_spend(
            model=payload.model, api_provider=api_provider,
            api_key=auth.api_key, user=auth.user_id, team=auth.team_id, spend=request_cost,
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
                call_type="audio_speech",
                usage=usage,
                cost=request_cost,
                metadata=spend_metadata,
                cache_hit=False,
                start_time=callback_start,
                end_time=datetime.now(tz=UTC),
            )
        )
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
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUDIO_SPEECH_REQUEST,
            status="success",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="model",
            resource_id=payload.model,
            request_payload=request_data,
            response_payload={"bytes": len(audio_bytes), "response_format": effective_format},
            metadata=attach_route_decision(
                {
                    "route": request.url.path,
                    "provider": api_provider,
                    "api_base": api_base,
                    "deployment_model": deployment_model,
                    "characters": usage["input_characters"],
                },
                request,
            ),
        )
        content_type = AUDIO_CONTENT_TYPES.get(effective_format, "audio/mpeg")
        return Response(content=audio_bytes, media_type=content_type, headers=route_decision_headers(request))
    except httpx.HTTPError as exc:
        await request.app.state.passive_health_tracker.record_request_outcome(primary.deployment_id, success=False, error=str(exc))
        status_code = getattr(getattr(exc, "response", None), "status_code", 502)
        increment_request(model=payload.model, api_provider=api_provider, api_key=auth.api_key, user=auth.user_id, team=auth.team_id, status_code=status_code)
        increment_request_failure(model=payload.model, api_provider=api_provider, error_type=exc.__class__.__name__)
        observe_request_latency(model=payload.model, api_provider=api_provider, status_code=status_code, latency_seconds=perf_counter() - request_start)
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUDIO_SPEECH_REQUEST,
            status="error",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="model",
            resource_id=payload.model,
            request_payload=request_data,
            error=exc,
            metadata=attach_route_decision({"route": request.url.path, "provider": api_provider}, request),
        )
        raise InvalidRequestError(message=f"Audio speech request failed: {exc}") from exc
    except Exception as exc:
        await request.app.state.passive_health_tracker.record_request_outcome(primary.deployment_id, success=False, error=str(exc))
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUDIO_SPEECH_REQUEST,
            status="error",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="model",
            resource_id=payload.model,
            request_payload=request_data,
            error=exc,
            metadata=attach_route_decision({"route": request.url.path, "provider": api_provider}, request),
        )
        raise


async def _execute_gemini_tts(
    *,
    request: Request,
    payload: AudioSpeechRequest,
    deployment: Deployment,
    api_base: str,
    api_key: str,
) -> dict[str, Any]:
    effective_format = _resolve_gemini_response_format(payload)
    upstream_model = resolve_upstream_model(deployment.deltallm_params)
    endpoint = f"{api_base}/models/{upstream_model}:generateContent?key={api_key}"
    upstream_payload = _build_gemini_tts_payload(payload=payload, response_format=effective_format)
    from src.routers.utils import apply_default_params

    apply_default_params(upstream_payload, deployment.model_info)

    upstream_start = perf_counter()
    response = await request.app.state.http_client.post(
        endpoint,
        headers={"Content-Type": "application/json"},
        json=upstream_payload,
        timeout=deployment.deltallm_params.get("timeout") or 300,
    )
    if response.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"Upstream Gemini TTS call failed with status {response.status_code}",
            request=httpx.Request("POST", endpoint),
            response=response,
        )

    data = response.json()
    audio_bytes = _extract_gemini_audio_bytes(data, response_format=effective_format)
    return {
        "_audio_bytes": audio_bytes,
        "_billing_payload": _extract_gemini_billing_payload(data),
        "_api_latency_ms": (perf_counter() - upstream_start) * 1000,
        "_api_base": api_base,
        "_deployment_model": deployment.deltallm_params.get("model"),
        "_model_info": deployment.model_info,
        "_response_format": effective_format,
    }


def _should_force_tts_sse(*, model_info: dict[str, Any] | None, provider: str) -> bool:
    normalized_provider = (provider or "").strip().lower()
    if normalized_provider not in SSE_USAGE_PROVIDERS:
        return False

    info = dict(model_info or {})
    has_token_or_second_pricing = any(
        float(info.get(field) or 0) > 0
        for field in (
            "input_cost_per_token",
            "output_cost_per_token",
            "input_cost_per_audio_token",
            "output_cost_per_audio_token",
            "input_cost_per_second",
            "output_cost_per_second",
        )
    )
    has_character_pricing = any(
        float(info.get(field) or 0) > 0
        for field in ("input_cost_per_character", "output_cost_per_character")
    )
    return has_token_or_second_pricing and not has_character_pricing


def _resolve_gemini_response_format(payload: AudioSpeechRequest) -> str:
    requested_format = (payload.response_format or "mp3").strip().lower()
    if requested_format in GEMINI_SUPPORTED_RESPONSE_FORMATS:
        return requested_format
    if "response_format" not in payload.model_fields_set:
        return "wav"
    raise InvalidRequestError(message="Gemini TTS supports only 'wav' and 'pcm' response_format values")


def _build_gemini_tts_payload(*, payload: AudioSpeechRequest, response_format: str) -> dict[str, Any]:
    generation_config: dict[str, Any] = {
        "responseModalities": ["AUDIO"],
        "speechConfig": {
            "voiceConfig": {
                "prebuiltVoiceConfig": {
                    "voiceName": payload.voice,
                }
            }
        },
    }
    if response_format == "pcm":
        generation_config["audioConfig"] = {"audioEncoding": "LINEAR16"}

    return {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": payload.input}],
            }
        ],
        "generationConfig": generation_config,
    }


def _extract_gemini_audio_bytes(response_payload: dict[str, Any], *, response_format: str) -> bytes:
    parts = (((response_payload.get("candidates") or [{}])[0]).get("content") or {}).get("parts") or []
    for part in parts:
        if not isinstance(part, dict):
            continue
        inline_data = part.get("inlineData") or part.get("inline_data")
        if not isinstance(inline_data, dict):
            continue
        encoded_audio = inline_data.get("data")
        if not isinstance(encoded_audio, str) or not encoded_audio:
            continue
        pcm_bytes = base64.b64decode(encoded_audio)
        if response_format == "pcm":
            return pcm_bytes
        mime_type = str(inline_data.get("mimeType") or inline_data.get("mime_type") or "")
        sample_rate_hz = _extract_pcm_sample_rate(mime_type) or 24000
        return _wrap_pcm_as_wav(pcm_bytes, sample_rate_hz=sample_rate_hz)
    raise InvalidRequestError(message="Gemini TTS response did not include audio data")


def _extract_gemini_billing_payload(response_payload: dict[str, Any]) -> dict[str, Any] | None:
    usage = response_payload.get("usageMetadata")
    if not isinstance(usage, dict):
        return None

    prompt_tokens = int(usage.get("promptTokenCount") or 0)
    completion_tokens = int(usage.get("candidatesTokenCount") or 0)
    transformed_usage: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    output_audio_tokens = _extract_gemini_modality_token_count(usage.get("candidatesTokensDetails"), "AUDIO")
    if output_audio_tokens is None and completion_tokens > 0:
        output_audio_tokens = completion_tokens
    if output_audio_tokens is not None:
        transformed_usage["output_audio_tokens"] = output_audio_tokens
    return {"usage": transformed_usage}


def _extract_gemini_modality_token_count(details: Any, modality: str) -> int | None:
    if not isinstance(details, list):
        return None
    target = modality.strip().upper()
    for detail in details:
        if not isinstance(detail, dict):
            continue
        if str(detail.get("modality") or "").strip().upper() != target:
            continue
        try:
            return int(detail.get("tokenCount") or 0)
        except (TypeError, ValueError):
            return None
    return None


def _extract_pcm_sample_rate(mime_type: str) -> int | None:
    marker = "rate="
    if marker not in mime_type:
        return None
    try:
        return int(mime_type.split(marker, 1)[1].split(";", 1)[0].strip())
    except (TypeError, ValueError):
        return None


def _wrap_pcm_as_wav(pcm_bytes: bytes, *, sample_rate_hz: int) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate_hz)
        wav_file.writeframes(pcm_bytes)
    return output.getvalue()


def _extract_sse_audio_and_usage(payload: bytes) -> tuple[bytes, dict[str, Any] | None]:
    events = _parse_sse_events(payload)
    if not events:
        return payload, None

    audio_chunks: list[bytes] = []
    usage_payload: dict[str, Any] | None = None
    duration_seconds: float | None = None
    for event in events:
        event_type = str(event.get("type") or "")
        if event_type == "speech.audio.delta":
            encoded_audio = event.get("audio")
            if isinstance(encoded_audio, str) and encoded_audio:
                try:
                    audio_chunks.append(base64.b64decode(encoded_audio))
                except (ValueError, TypeError):
                    continue
        elif event_type == "speech.audio.done":
            usage = event.get("usage")
            if isinstance(usage, dict):
                usage_payload = usage
            duration_value = event.get("duration_seconds")
            if duration_value not in (None, ""):
                try:
                    duration_seconds = float(duration_value)
                except (TypeError, ValueError):
                    duration_seconds = None

    if not audio_chunks and usage_payload is None and duration_seconds is None:
        return payload, None

    billing_payload: dict[str, Any] = {}
    if usage_payload is not None:
        billing_payload["usage"] = usage_payload
    if duration_seconds is not None and duration_seconds > 0:
        billing_payload["duration"] = duration_seconds
    return b"".join(audio_chunks), billing_payload or None


def _parse_sse_events(payload: bytes) -> list[dict[str, Any]]:
    text = payload.decode("utf-8", errors="ignore")
    events: list[dict[str, Any]] = []
    for chunk in text.split("\n\n"):
        event_name: str | None = None
        data_lines: list[str] = []
        for raw_line in chunk.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())

        if not data_lines:
            continue
        data_str = "\n".join(data_lines)
        if data_str == "[DONE]":
            continue
        try:
            event = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            if event_name and "type" not in event:
                event["type"] = event_name
            events.append(event)
    return events
