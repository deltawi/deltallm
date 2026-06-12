from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from src.billing.audio_usage import billing_metadata, normalize_transcription_usage
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
from src.models.errors import InvalidRequestError
from src.providers.resolution import (
    is_openai_compatible_provider,
    resolve_provider,
    resolve_upstream_model,
)
from src.upstream_auth import build_openai_compatible_auth_headers
from src.upstream_http import build_upstream_request_timeout_for_request, configured_timeout_seconds
from src.router.router import Deployment
from src.router.usage import record_router_usage
from src.audit.actions import AuditAction
from src.telemetry.request_failures import enqueue_request_log_write, seed_request_failure_context
from src.routers.audit_helpers import emit_audit_event
from src.routers.routing_decision import (
    attach_route_decision,
    capture_attempted_deployment,
    capture_initial_route_decision,
    route_failover_kwargs,
    route_decision_headers,
    resolve_failure_target,
    update_served_route_decision,
)
from src.routers.utils import enforce_budget_if_configured, fire_and_forget
from src.services.model_visibility import ensure_model_allowed, get_callable_target_policy_mode_from_app

DEFAULT_AUDIO_TRANSCRIPTION_TIMEOUT_SECONDS = 600.0
ELEVENLABS_STT_FORM_DEFAULT_KEYS = {
    "tag_audio_events",
    "num_speakers",
    "timestamps_granularity",
    "diarize",
    "diarization_threshold",
    "file_format",
    "seed",
    "use_multi_channel",
    "no_verbatim",
}
ELEVENLABS_STT_QUERY_DEFAULT_KEYS = {"enable_logging"}

router = APIRouter(prefix="/v1", tags=["audio"])


def _transcription_timeout_seconds(params: dict[str, Any]) -> float:
    return configured_timeout_seconds(params.get("timeout")) or DEFAULT_AUDIO_TRANSCRIPTION_TIMEOUT_SECONDS


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
    params = deployment.deltallm_params
    api_key = params.get("api_key")
    if not api_key:
        raise InvalidRequestError(message="Provider API key is missing for selected model")

    api_base = params.get("api_base", request.app.state.settings.openai_base_url).rstrip("/")
    api_provider = resolve_provider(params)
    if api_provider == "elevenlabs":
        return await _execute_elevenlabs_stt(
            request=request,
            file_content=file_content,
            filename=filename,
            content_type=content_type,
            model=model,
            language=language,
            response_format=response_format,
            temperature=temperature,
            deployment=deployment,
            api_base=api_base,
            api_key=str(api_key),
        )

    headers = build_openai_compatible_auth_headers(
        provider=api_provider,
        api_key=str(api_key),
        auth_header_name=params.get("auth_header_name"),
        auth_header_format=params.get("auth_header_format"),
    )
    upstream_response_format = _resolve_upstream_response_format(
        requested_response_format=response_format,
        model_info=deployment.model_info,
        provider=api_provider,
    )

    form_model = resolve_upstream_model(params, fallback_model=model) or model

    from src.routers.utils import apply_default_params
    _stt_defaults: dict[str, Any] = {"model": form_model}
    if language:
        _stt_defaults["language"] = language
    if prompt:
        _stt_defaults["prompt"] = prompt
    if upstream_response_format:
        _stt_defaults["response_format"] = upstream_response_format
    if temperature is not None:
        _stt_defaults["temperature"] = str(temperature)
    apply_default_params(_stt_defaults, deployment.model_info)

    form_data: dict[str, str] = {}
    for _fk, _fv in _stt_defaults.items():
        form_data[_fk] = str(_fv) if _fv is not None else ""

    upstream_start = perf_counter()
    timeout_seconds = _transcription_timeout_seconds(params)
    response = await request.app.state.http_client.post(
        f"{api_base}/audio/transcriptions",
        headers=headers,
        files={"file": (filename, file_content, content_type)},
        data=form_data,
        timeout=build_upstream_request_timeout_for_request(request, timeout_seconds),
    )
    if response.status_code >= 400:
        try:
            upstream_body = response.json()
            upstream_msg = upstream_body.get("error", {}).get("message", response.text)
        except Exception:
            upstream_msg = response.text
        import logging
        logging.getLogger(__name__).warning(
            "upstream STT %s returned %d: %s", api_base, response.status_code, upstream_msg
        )
        raise httpx.HTTPStatusError(
            f"Upstream transcription failed with status {response.status_code}: {upstream_msg}",
            request=httpx.Request("POST", f"{api_base}/audio/transcriptions"),
            response=response,
        )
    parsed_response = response.json() if "json" in upstream_response_format else {"text": response.text}
    data = _reshape_transcription_response(
        requested_response_format=response_format,
        upstream_response_format=upstream_response_format,
        response_payload=parsed_response,
    )
    if parsed_response is not data:
        data["_billing_payload"] = parsed_response
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
    seed_request_failure_context(
        request,
        call_type="audio_transcription",
        model=model,
        request_start=request_start,
        audit_action=AuditAction.AUDIO_TRANSCRIPTION_REQUEST,
    )
    auth = request.state.user_api_key
    ensure_model_allowed(
        auth,
        model,
        callable_target_grant_service=getattr(request.app.state, "callable_target_grant_service", None),
        policy_mode=get_callable_target_policy_mode_from_app(request.app),
        emit_shadow_log=True,
    )
    await enforce_budget_if_configured(request, model=model, auth=auth)

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
    failover_kwargs = route_failover_kwargs(request_context)
    if "timeout_seconds" not in failover_kwargs:
        def timeout_for_deployment(deployment: Deployment) -> float:
            return _transcription_timeout_seconds(deployment.deltallm_params)

        failover_kwargs["timeout_for_deployment"] = timeout_for_deployment
    capture_initial_route_decision(request, request_context)
    api_provider = resolve_provider(primary.deltallm_params)
    request_id = request.headers.get("x-request-id")
    primary_api_base = str(primary.deltallm_params.get("api_base", request.app.state.settings.openai_base_url)).rstrip("/")

    def track_attempt(deployment):  # noqa: ANN001
        capture_attempted_deployment(request, deployment)

    try:
        data, served_deployment = await request.app.state.failover_manager.execute_with_failover(
            primary_deployment=primary,
            model_group=model_group,
            execute=lambda dep: _execute_stt(
                request, file_content, filename, content_type_str,
                model, language, prompt, response_format, temperature, dep,
            ),
            return_deployment=True,
            on_attempt=track_attempt,
            **failover_kwargs,
        )
        update_served_route_decision(
            request,
            primary_deployment_id=primary.deployment_id,
            served_deployment_id=served_deployment.deployment_id,
        )
        await request.app.state.passive_health_tracker.record_request_outcome(served_deployment.deployment_id, success=True)
        api_provider = resolve_provider(served_deployment.deltallm_params)

        api_latency_ms = data.pop("_api_latency_ms", 0)
        api_base = data.pop("_api_base", "")
        deployment_model = data.pop("_deployment_model", None)
        model_info = data.pop("_model_info", {})
        billing_payload = data.pop("_billing_payload", data)

        usage = normalize_transcription_usage(
            response_payload=billing_payload,
            file_size_bytes=len(file_content),
            provider=api_provider,
        )
        await record_router_usage(
            request.app.state.router_state_backend,
            served_deployment.deployment_id,
            mode="audio_transcription",
            usage=usage,
        )
        billing = compute_billing_result(mode="audio_transcription", usage=usage, model_info=model_info)
        request_cost = billing.cost
        spend_metadata = attach_route_decision(
            {
                "api_base": api_base,
                "provider": api_provider,
                "deployment_model": deployment_model,
                "billing": billing_metadata(billing),
            },
            request,
        )
        increment_request(
            model=model, api_provider=api_provider,
            api_key=auth.api_key, user=auth.user_id, team=auth.team_id, status_code=200,
        )
        increment_spend(
            model=model, api_provider=api_provider,
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
                model=model,
                call_type="audio_transcription",
                usage=usage,
                cost=request_cost,
                metadata=spend_metadata,
                cache_hit=False,
                start_time=callback_start,
                end_time=datetime.now(tz=UTC),
            )
        )
        observe_request_latency(model=model, api_provider=api_provider, status_code=200, latency_seconds=perf_counter() - request_start)
        observe_api_latency(model=model, api_provider=api_provider, latency_seconds=api_latency_ms / 1000)
        callback_payload = build_standard_logging_payload(
            call_type="audio_transcription", request_id=request_id, model=model,
            deployment_model=deployment_model, request_payload=request_data, response_obj=data,
            user_api_key_dict=auth.model_dump(mode="json"), start_time=callback_start, end_time=datetime.now(tz=UTC),
            api_base=api_base, response_cost=request_cost, api_latency_ms=api_latency_ms,
            api_provider=api_provider,
            turn_off_message_logging=bool(getattr(request.app.state, "turn_off_message_logging", False)),
        )
        callback_manager.dispatch_success_callbacks(callback_payload)
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUDIO_TRANSCRIPTION_REQUEST,
            status="success",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="model",
            resource_id=model,
            request_payload=request_data,
            response_payload=data,
            metadata=attach_route_decision(
                {
                    "route": request.url.path,
                    "provider": api_provider,
                    "api_base": api_base,
                    "deployment_model": deployment_model,
                    "file_size_bytes": len(file_content),
                },
                request,
            ),
        )
        return JSONResponse(status_code=200, content=data, headers=route_decision_headers(request))
    except httpx.HTTPError as exc:
        failure_target = resolve_failure_target(request, fallback_deployment=primary)
        failure_deployment_id = str(failure_target.deployment_id or primary.deployment_id)
        failure_provider = str(failure_target.provider or api_provider)
        failure_api_base = failure_target.api_base or primary_api_base
        failure_deployment_model = failure_target.deployment_model or primary.deltallm_params.get("model")
        await request.app.state.passive_health_tracker.record_request_outcome(
            failure_deployment_id,
            success=False,
            error=str(exc),
            exc=exc,
        )
        status_code = getattr(getattr(exc, "response", None), "status_code", 502)
        increment_request(model=model, api_provider=failure_provider, api_key=auth.api_key, user=auth.user_id, team=auth.team_id, status_code=status_code)
        increment_request_failure(model=model, api_provider=failure_provider, error_type=exc.__class__.__name__)
        observe_request_latency(model=model, api_provider=failure_provider, status_code=status_code, latency_seconds=perf_counter() - request_start)
        enqueue_request_log_write(
            request,
            request.app.state.spend_tracking_service.log_request_failure(
                request_id=request_id or "",
                api_key=auth.api_key,
                user_id=auth.user_id,
                team_id=auth.team_id,
                organization_id=getattr(auth, "organization_id", None),
                end_user_id=None,
                model=model,
                call_type="audio_transcription",
                metadata=attach_route_decision(
                    {
                        "route": request.url.path,
                        "provider": failure_provider,
                        "api_base": failure_api_base,
                        "deployment_model": failure_deployment_model,
                        "file_size_bytes": len(file_content),
                    },
                    request,
                ),
                cache_hit=False,
                start_time=callback_start,
                end_time=datetime.now(tz=UTC),
                http_status_code=status_code,
                exc=exc,
            )
        )
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUDIO_TRANSCRIPTION_REQUEST,
            status="error",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="model",
            resource_id=model,
            request_payload=request_data,
            error=exc,
            metadata=attach_route_decision(
                {
                    "route": request.url.path,
                    "provider": failure_provider,
                    "api_base": failure_api_base,
                    "deployment_model": failure_deployment_model,
                    "file_size_bytes": len(file_content),
                },
                request,
            ),
        )
        raise InvalidRequestError(message=f"Audio transcription request failed: {exc}") from exc
    except Exception as exc:
        failure_target = resolve_failure_target(request, fallback_deployment=primary)
        failure_deployment_id = str(failure_target.deployment_id or primary.deployment_id)
        failure_provider = str(failure_target.provider or api_provider)
        failure_api_base = failure_target.api_base or primary_api_base
        failure_deployment_model = failure_target.deployment_model or primary.deltallm_params.get("model")
        await request.app.state.passive_health_tracker.record_request_outcome(
            failure_deployment_id,
            success=False,
            error=str(exc),
            exc=exc,
        )
        status_code = int(getattr(exc, "status_code", 500) or 500)
        enqueue_request_log_write(
            request,
            request.app.state.spend_tracking_service.log_request_failure(
                request_id=request_id or "",
                api_key=auth.api_key,
                user_id=auth.user_id,
                team_id=auth.team_id,
                organization_id=getattr(auth, "organization_id", None),
                end_user_id=None,
                model=model,
                call_type="audio_transcription",
                metadata=attach_route_decision(
                    {
                        "route": request.url.path,
                        "provider": failure_provider,
                        "api_base": failure_api_base,
                        "deployment_model": failure_deployment_model,
                        "file_size_bytes": len(file_content),
                    },
                    request,
                ),
                cache_hit=False,
                start_time=callback_start,
                end_time=datetime.now(tz=UTC),
                http_status_code=status_code,
                exc=exc,
            )
        )
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.AUDIO_TRANSCRIPTION_REQUEST,
            status="error",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="model",
            resource_id=model,
            request_payload=request_data,
            error=exc,
            metadata=attach_route_decision(
                {
                    "route": request.url.path,
                    "provider": failure_provider,
                    "api_base": failure_api_base,
                    "deployment_model": failure_deployment_model,
                    "file_size_bytes": len(file_content),
                },
                request,
            ),
        )
        raise


def _resolve_upstream_response_format(
    *,
    requested_response_format: str | None,
    model_info: dict[str, Any] | None,
    provider: str,
) -> str:
    normalized_format = (requested_response_format or "json").strip().lower() or "json"
    if normalized_format == "verbose_json":
        return "verbose_json"
    if normalized_format not in {"json", "text", "srt", "vtt"}:
        return normalized_format
    if not is_openai_compatible_provider(provider):
        return normalized_format

    info = dict(model_info or {})
    has_second_pricing = any(
        float(info.get(field) or 0) > 0
        for field in ("input_cost_per_second", "output_cost_per_second")
    )
    return "verbose_json" if has_second_pricing else normalized_format


def _reshape_transcription_response(
    *,
    requested_response_format: str | None,
    upstream_response_format: str,
    response_payload: dict[str, Any],
) -> dict[str, Any]:
    normalized_format = (requested_response_format or "json").strip().lower() or "json"
    if upstream_response_format != "verbose_json":
        return response_payload
    if normalized_format == "verbose_json":
        return response_payload

    transcript_text = str(response_payload.get("text") or "")
    if normalized_format == "json":
        return {"text": transcript_text}
    if normalized_format == "text":
        return {"text": transcript_text}
    if normalized_format == "srt":
        return {"text": _render_srt(response_payload)}
    if normalized_format == "vtt":
        return {"text": _render_vtt(response_payload)}
    return response_payload


async def _execute_elevenlabs_stt(
    *,
    request: Request,
    file_content: bytes,
    filename: str,
    content_type: str,
    model: str,
    language: str | None,
    response_format: str | None,
    temperature: float | None,
    deployment: Deployment,
    api_base: str,
    api_key: str,
) -> dict[str, Any]:
    defaults = _model_default_params(deployment.model_info)
    upstream_model = resolve_upstream_model(deployment.deltallm_params, fallback_model=model) or model
    form_data = _build_elevenlabs_stt_form_data(
        model_id=upstream_model,
        language=language,
        temperature=temperature,
        default_params=defaults,
    )
    endpoint = f"{api_base}/speech-to-text"
    query = _build_elevenlabs_stt_query(default_params=defaults)
    if query:
        endpoint = f"{endpoint}?{query}"

    upstream_start = perf_counter()
    response = await request.app.state.http_client.post(
        endpoint,
        headers={"xi-api-key": api_key},
        files={"file": (filename, file_content, content_type)},
        data=form_data,
        timeout=build_upstream_request_timeout_for_request(
            request,
            _transcription_timeout_seconds(deployment.deltallm_params),
        ),
    )
    if response.status_code >= 400:
        raise httpx.HTTPStatusError(
            _format_elevenlabs_stt_error(response),
            request=httpx.Request("POST", endpoint),
            response=response,
        )

    try:
        parsed_response = response.json()
    except ValueError as exc:
        error_request = httpx.Request("POST", endpoint)
        error_response = httpx.Response(
            502,
            text="Upstream ElevenLabs STT call returned invalid JSON",
            request=error_request,
        )
        raise httpx.HTTPStatusError(
            "Upstream ElevenLabs STT call returned invalid JSON",
            request=error_request,
            response=error_response,
        ) from exc

    data, billing_payload = _reshape_elevenlabs_transcription_response(
        requested_response_format=response_format,
        response_payload=parsed_response,
    )
    data["_billing_payload"] = billing_payload
    data["_api_latency_ms"] = (perf_counter() - upstream_start) * 1000
    data["_api_base"] = api_base
    data["_deployment_model"] = deployment.deltallm_params.get("model")
    data["_model_info"] = deployment.model_info
    return data


def _model_default_params(model_info: dict[str, Any] | None) -> dict[str, Any]:
    defaults = (model_info or {}).get("default_params")
    return dict(defaults) if isinstance(defaults, Mapping) else {}


def _build_elevenlabs_stt_form_data(
    *,
    model_id: str,
    language: str | None,
    temperature: float | None,
    default_params: Mapping[str, Any],
) -> dict[str, str]:
    form_data: dict[str, Any] = {"model_id": model_id}
    for key in ELEVENLABS_STT_FORM_DEFAULT_KEYS:
        if key in default_params and default_params[key] is not None:
            form_data[key] = default_params[key]

    if language:
        form_data["language_code"] = language
    elif default_params.get("language_code"):
        form_data["language_code"] = default_params["language_code"]

    if temperature is not None:
        form_data["temperature"] = temperature
    elif default_params.get("temperature") is not None:
        form_data["temperature"] = default_params["temperature"]

    return {
        key: _stringify_elevenlabs_form_value(value)
        for key, value in form_data.items()
        if value is not None
    }


def _build_elevenlabs_stt_query(*, default_params: Mapping[str, Any]) -> str:
    query_params: dict[str, str] = {}
    for key in ELEVENLABS_STT_QUERY_DEFAULT_KEYS:
        value = default_params.get(key)
        if value is None:
            continue
        query_params[key] = _stringify_elevenlabs_form_value(value)
    return urlencode(query_params)


def _stringify_elevenlabs_form_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _reshape_elevenlabs_transcription_response(
    *,
    requested_response_format: str | None,
    response_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized_payload = _normalize_elevenlabs_transcript_payload(response_payload)
    duration_seconds = _extract_elevenlabs_duration_seconds(response_payload)
    normalized_payload["duration"] = duration_seconds or 0.0

    billing_payload = dict(normalized_payload)
    if duration_seconds is not None and duration_seconds > 0:
        billing_payload["_billing_duration_seconds"] = duration_seconds
        billable_duration_seconds = _extract_elevenlabs_billable_duration_seconds(
            response_payload=response_payload,
            duration_seconds=duration_seconds,
        )
        if billable_duration_seconds > duration_seconds:
            billing_payload["_billing_billable_duration_seconds"] = billable_duration_seconds

    normalized_format = (requested_response_format or "json").strip().lower() or "json"
    transcript_text = str(normalized_payload.get("text") or "")
    if normalized_format == "json":
        return {"text": transcript_text}, billing_payload
    if normalized_format == "text":
        return {"text": transcript_text}, billing_payload
    if normalized_format == "srt":
        return {"text": _render_srt(normalized_payload)}, billing_payload
    if normalized_format == "vtt":
        return {"text": _render_vtt(normalized_payload)}, billing_payload
    return normalized_payload, billing_payload


def _normalize_elevenlabs_transcript_payload(response_payload: dict[str, Any]) -> dict[str, Any]:
    transcripts = response_payload.get("transcripts")
    transcript_payloads = (
        [item for item in transcripts if isinstance(item, Mapping)]
        if isinstance(transcripts, list)
        else [response_payload]
    )

    text_parts: list[str] = []
    all_words: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    language: str | None = None
    language_probability: Any = None

    for index, transcript in enumerate(transcript_payloads):
        transcript_text = str(transcript.get("text") or "").strip()
        if transcript_text:
            text_parts.append(transcript_text)

        if language is None and transcript.get("language_code"):
            language = str(transcript.get("language_code"))
        if language_probability is None and transcript.get("language_probability") is not None:
            language_probability = transcript.get("language_probability")

        channel_index = transcript.get("channel_index")
        if channel_index is None and isinstance(transcripts, list):
            channel_index = index
        words = _normalize_elevenlabs_words(transcript.get("words"), channel_index=channel_index)
        all_words.extend(words)
        segments.extend(_elevenlabs_words_to_segments(words))

    if not text_parts and all_words:
        text_parts.append(_join_elevenlabs_tokens(str(word.get("text") or word.get("word") or "") for word in all_words))

    normalized: dict[str, Any] = {"text": "\n".join(text_parts)}
    if language:
        normalized["language"] = language
    if language_probability is not None:
        normalized["language_probability"] = language_probability
    if response_payload.get("transcription_id") is not None:
        normalized["transcription_id"] = response_payload["transcription_id"]
    if all_words:
        normalized["words"] = all_words
    normalized["segments"] = segments
    if isinstance(transcripts, list):
        normalized["transcripts"] = transcripts
    if response_payload.get("entities") is not None:
        normalized["entities"] = response_payload["entities"]
    if response_payload.get("additional_formats") is not None:
        normalized["additional_formats"] = response_payload["additional_formats"]
    return normalized


def _normalize_elevenlabs_words(words: Any, *, channel_index: Any = None) -> list[dict[str, Any]]:
    if not isinstance(words, list):
        return []

    normalized_words: list[dict[str, Any]] = []
    for word in words:
        if not isinstance(word, Mapping):
            continue
        text = str(word.get("text") or word.get("word") or "")
        normalized: dict[str, Any] = {"text": text, "word": text}
        start = _float_or_none(word.get("start"))
        end = _float_or_none(word.get("end"))
        if start is not None:
            normalized["start"] = start
        if end is not None:
            normalized["end"] = end
        for key in ("type", "speaker_id", "logprob"):
            if word.get(key) is not None:
                normalized[key] = word[key]
        if channel_index is not None:
            normalized["channel_index"] = channel_index
        if word.get("characters") is not None:
            normalized["characters"] = word["characters"]
        normalized_words.append(normalized)
    return normalized_words


def _elevenlabs_words_to_segments(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current_words: list[str] = []
    current_start: float | None = None
    current_end: float | None = None
    current_speaker: Any = None
    current_channel: Any = None

    def flush_segment() -> None:
        nonlocal current_words, current_start, current_end, current_speaker, current_channel
        if not current_words or current_start is None or current_end is None:
            current_words = []
            current_start = None
            current_end = None
            current_speaker = None
            current_channel = None
            return
        segment: dict[str, Any] = {
            "start": current_start,
            "end": current_end,
            "text": _join_elevenlabs_tokens(current_words),
        }
        if current_speaker is not None:
            segment["speaker"] = current_speaker
        if current_channel is not None:
            segment["channel_index"] = current_channel
        segments.append(segment)
        current_words = []
        current_start = None
        current_end = None
        current_speaker = None
        current_channel = None

    for word in words:
        text = str(word.get("text") or word.get("word") or "").strip()
        start = _float_or_none(word.get("start"))
        end = _float_or_none(word.get("end"))
        if not text or start is None or end is None:
            continue

        speaker = word.get("speaker_id")
        channel = word.get("channel_index")
        should_flush = bool(current_words) and (
            speaker != current_speaker
            or channel != current_channel
            or (current_start is not None and start - current_start >= 5.0)
        )
        if should_flush:
            flush_segment()

        if not current_words:
            current_start = start
            current_speaker = speaker
            current_channel = channel
        current_words.append(text)
        current_end = max(end, current_end or end)
        if text.endswith((".", "!", "?")):
            flush_segment()

    flush_segment()
    return segments


def _extract_elevenlabs_duration_seconds(response_payload: dict[str, Any]) -> float | None:
    duration_seconds = _float_or_none(response_payload.get("duration_seconds"))
    if duration_seconds is not None and duration_seconds > 0:
        return duration_seconds

    transcripts = response_payload.get("transcripts")
    if isinstance(transcripts, list):
        transcript_duration = max(
            (
                duration
                for item in transcripts
                if isinstance(item, Mapping)
                for duration in [_float_or_none(item.get("duration_seconds"))]
                if duration is not None
            ),
            default=None,
        )
        if transcript_duration is not None and transcript_duration > 0:
            return transcript_duration

    top_level_word_end = _max_elevenlabs_word_end(response_payload.get("words"))
    if top_level_word_end is not None and top_level_word_end > 0:
        return top_level_word_end

    if isinstance(transcripts, list):
        transcript_word_end = max(
            (
                word_end
                for item in transcripts
                if isinstance(item, Mapping)
                for word_end in [_max_elevenlabs_word_end(item.get("words"))]
                if word_end is not None
            ),
            default=None,
        )
        if transcript_word_end is not None and transcript_word_end > 0:
            return transcript_word_end

    return None


def _extract_elevenlabs_billable_duration_seconds(
    *,
    response_payload: dict[str, Any],
    duration_seconds: float,
) -> float:
    transcripts = response_payload.get("transcripts")
    if not isinstance(transcripts, list):
        return duration_seconds
    channel_count = sum(1 for item in transcripts if isinstance(item, Mapping))
    if channel_count <= 1:
        return duration_seconds
    return duration_seconds * channel_count


def _max_elevenlabs_word_end(words: Any) -> float | None:
    if not isinstance(words, list):
        return None
    return max(
        (
            end
            for word in words
            if isinstance(word, Mapping)
            for end in [_float_or_none(word.get("end"))]
            if end is not None
        ),
        default=None,
    )


def _join_elevenlabs_tokens(tokens: Any) -> str:
    text = ""
    for token in tokens:
        value = str(token or "").strip()
        if not value:
            continue
        if not text:
            text = value
        elif value in {".", ",", "!", "?", ";", ":", "%", ")", "]", "}"}:
            text = f"{text}{value}"
        elif text.endswith(("(", "[", "{")):
            text = f"{text}{value}"
        else:
            text = f"{text} {value}"
    return text


def _format_elevenlabs_stt_error(response: httpx.Response) -> str:
    upstream_msg = response.text
    try:
        upstream_body = response.json()
    except Exception:
        upstream_body = None

    if isinstance(upstream_body, Mapping):
        detail = upstream_body.get("detail")
        if isinstance(detail, Mapping):
            upstream_msg = str(detail.get("message") or detail.get("detail") or upstream_msg)
        elif detail:
            upstream_msg = str(detail)
        else:
            upstream_msg = str(upstream_body.get("message") or upstream_msg)

    return f"Upstream ElevenLabs STT call failed with status {response.status_code}: {upstream_msg}"


def _render_srt(response_payload: dict[str, Any]) -> str:
    segments = response_payload.get("segments")
    if not isinstance(segments, list) or not segments:
        return str(response_payload.get("text") or "")

    lines: list[str] = []
    for index, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = _format_timestamp(segment.get("start"), decimal_separator=",")
        end = _format_timestamp(segment.get("end"), decimal_separator=",")
        lines.extend([str(index), f"{start} --> {end}", text, ""])
    return "\n".join(lines).strip()


def _render_vtt(response_payload: dict[str, Any]) -> str:
    segments = response_payload.get("segments")
    if not isinstance(segments, list) or not segments:
        transcript_text = str(response_payload.get("text") or "")
        return f"WEBVTT\n\n{transcript_text}".strip()

    lines = ["WEBVTT", ""]
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = _format_timestamp(segment.get("start"), decimal_separator=".")
        end = _format_timestamp(segment.get("end"), decimal_separator=".")
        lines.extend([f"{start} --> {end}", text, ""])
    return "\n".join(lines).strip()


def _format_timestamp(value: Any, *, decimal_separator: str) -> str:
    total_seconds = max(0.0, float(value or 0))
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    milliseconds = int(round((total_seconds - int(total_seconds)) * 1000))

    if milliseconds == 1000:
        milliseconds = 0
        seconds += 1
    if seconds == 60:
        seconds = 0
        minutes += 1
    if minutes == 60:
        minutes = 0
        hours += 1

    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{decimal_separator}{milliseconds:03d}"


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
