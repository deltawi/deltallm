from __future__ import annotations

from collections.abc import Mapping
from time import perf_counter
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import Request

from src.audio.transcription_formats import render_srt, render_vtt
from src.providers.base import parse_provider_json_response, validate_provider_success_payload
from src.providers.resolution import resolve_upstream_model
from src.router.router import Deployment
from src.upstream_http import build_upstream_request_timeout_for_request

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


def _is_valid_elevenlabs_stt_success_payload(data: Mapping[str, Any]) -> bool:
    transcripts = data.get("transcripts")
    if transcripts is not None:
        return (
            isinstance(transcripts, list)
            and bool(transcripts)
            and all(
                isinstance(transcript, Mapping)
                and "text" in transcript
                and isinstance(transcript.get("text"), str)
                for transcript in transcripts
            )
        )
    return "text" in data and isinstance(data.get("text"), str)


async def execute_elevenlabs_stt(
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
    timeout_seconds: float,
) -> dict[str, Any]:
    defaults = _model_default_params(deployment.model_info)
    upstream_model = (
        resolve_upstream_model(deployment.deltallm_params, fallback_model=model) or model
    )
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
        timeout=build_upstream_request_timeout_for_request(request, timeout_seconds),
    )
    if response.status_code >= 400:
        status_error = httpx.HTTPStatusError(
            f"Upstream ElevenLabs STT call failed with status {response.status_code}",
            request=httpx.Request("POST", endpoint),
            response=response,
        )
        raise request.app.state.provider_error_mapper_registry.map_error("elevenlabs", status_error)

    parsed_response = parse_provider_json_response(response)
    validate_provider_success_payload(
        parsed_response,
        _is_valid_elevenlabs_stt_success_payload,
    )

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
        return {"text": render_srt(normalized_payload)}, billing_payload
    if normalized_format == "vtt":
        return {"text": render_vtt(normalized_payload)}, billing_payload
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
        text_parts.append(
            _join_elevenlabs_tokens(
                str(word.get("text") or word.get("word") or "") for word in all_words
            )
        )

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


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
