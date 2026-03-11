from __future__ import annotations

from typing import Any, Mapping

from src.billing.cost import BillingResult


def normalize_transcription_usage(
    *,
    response_payload: Mapping[str, Any],
    file_size_bytes: int,
    provider: str | None = None,
) -> dict[str, Any]:
    usage: dict[str, Any] = {"file_size_bytes": file_size_bytes}
    provider_usage = response_payload.get("usage")
    if isinstance(provider_usage, Mapping):
        usage.update(_extract_token_usage(provider_usage))

    duration_seconds = _extract_duration_seconds(response_payload, provider_usage)
    if duration_seconds is not None and duration_seconds > 0:
        usage["duration_seconds"] = duration_seconds
        billable_duration_seconds = _apply_stt_provider_billing_rules(duration_seconds, provider)
        if billable_duration_seconds != duration_seconds:
            usage["billable_duration_seconds"] = billable_duration_seconds
    return usage


def normalize_speech_usage(
    *,
    request_text: str,
    response_payload: Mapping[str, Any] | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    usage: dict[str, Any] = {"input_characters": len(request_text)}
    provider_usage = response_payload.get("usage") if isinstance(response_payload, Mapping) else None
    if isinstance(provider_usage, Mapping):
        usage.update(_extract_speech_token_usage(provider_usage))

    duration_seconds = (
        _extract_duration_seconds(response_payload, provider_usage)
        if isinstance(response_payload, Mapping)
        else None
    )
    if duration_seconds is not None and duration_seconds > 0:
        usage["duration_seconds"] = duration_seconds
    return usage


def billing_metadata(result: BillingResult) -> dict[str, Any]:
    metadata: dict[str, Any] = {"cost": result.cost}
    if result.billing_unit is not None:
        metadata["billing_unit"] = result.billing_unit
    if result.pricing_fields_used:
        metadata["pricing_fields_used"] = list(result.pricing_fields_used)
    if result.usage_snapshot:
        metadata["usage_snapshot"] = result.usage_snapshot
    if result.unpriced_reason is not None:
        metadata["unpriced_reason"] = result.unpriced_reason
    return metadata


def _extract_token_usage(provider_usage: Mapping[str, Any]) -> dict[str, int]:
    prompt_tokens = max(0, int(provider_usage.get("prompt_tokens", provider_usage.get("input_tokens", 0)) or 0))
    completion_tokens = max(0, int(provider_usage.get("completion_tokens", provider_usage.get("output_tokens", 0)) or 0))
    usage: dict[str, int] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }

    prompt_details = provider_usage.get("prompt_tokens_details")
    if not isinstance(prompt_details, Mapping):
        prompt_details = provider_usage.get("input_token_details")
    if isinstance(prompt_details, Mapping):
        usage["input_audio_tokens"] = max(0, int(prompt_details.get("audio_tokens", 0) or 0))
    elif provider_usage.get("input_audio_tokens") is not None:
        usage["input_audio_tokens"] = max(0, int(provider_usage.get("input_audio_tokens", 0) or 0))

    completion_details = provider_usage.get("completion_tokens_details")
    if not isinstance(completion_details, Mapping):
        completion_details = provider_usage.get("output_token_details")
    if isinstance(completion_details, Mapping):
        usage["output_audio_tokens"] = max(0, int(completion_details.get("audio_tokens", 0) or 0))
    elif provider_usage.get("output_audio_tokens") is not None:
        usage["output_audio_tokens"] = max(0, int(provider_usage.get("output_audio_tokens", 0) or 0))

    return usage


def _extract_speech_token_usage(provider_usage: Mapping[str, Any]) -> dict[str, int]:
    usage = _extract_token_usage(provider_usage)
    output_audio_tokens = provider_usage.get("output_audio_tokens")
    if output_audio_tokens is None and provider_usage.get("output_tokens") is not None:
        output_audio_tokens = provider_usage.get("output_tokens")
    if output_audio_tokens is not None:
        usage["output_audio_tokens"] = max(0, int(output_audio_tokens or 0))
    return usage


def _extract_duration_seconds(
    response_payload: Mapping[str, Any],
    provider_usage: Mapping[str, Any] | None,
) -> float | None:
    duration_seconds = _first_float(
        response_payload.get("_billing_duration_seconds"),
        response_payload.get("duration"),
        response_payload.get("seconds"),
    )
    if duration_seconds is not None:
        return duration_seconds

    if isinstance(provider_usage, Mapping):
        duration_seconds = _first_float(
            provider_usage.get("seconds"),
            provider_usage.get("duration_seconds"),
            provider_usage.get("duration"),
        )
        if duration_seconds is not None:
            return duration_seconds

    for nested_key in ("metadata", "x_groq"):
        nested = response_payload.get(nested_key)
        if not isinstance(nested, Mapping):
            continue
        duration_seconds = _first_float(
            nested.get("duration"),
            nested.get("duration_seconds"),
            nested.get("seconds"),
        )
        if duration_seconds is not None:
            return duration_seconds

    return None


def _apply_stt_provider_billing_rules(duration_seconds: float, provider: str | None) -> float:
    normalized_provider = (provider or "").strip().lower()
    minimum_billed_seconds = 10.0 if normalized_provider == "groq" else 0.0
    return max(duration_seconds, minimum_billed_seconds)


def _first_float(*values: Any) -> float | None:
    for value in values:
        candidate = _float_or_none(value)
        if candidate is not None:
            return candidate
    return None


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
