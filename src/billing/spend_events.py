from __future__ import annotations

from datetime import datetime
from typing import Any

from src.providers.resolution import provider_from_model


def build_spend_event(
    *,
    request_id: str,
    api_key: str,
    user_id: str | None,
    team_id: str | None,
    organization_id: str | None,
    end_user_id: str | None,
    model: str,
    call_type: str,
    usage: dict[str, int] | None,
    cost: float,
    metadata: dict[str, Any] | None,
    cache_hit: bool,
    start_time: datetime,
    end_time: datetime,
    status: str = "success",
    http_status_code: int | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    usage_data = usage or {}
    meta = metadata if isinstance(metadata, dict) else {}
    billing = meta.get("billing") if isinstance(meta.get("billing"), dict) else {}
    usage_snapshot = billing.get("usage_snapshot") if isinstance(billing.get("usage_snapshot"), dict) else {}

    input_tokens = _int_value(usage_data.get("prompt_tokens"), usage_snapshot.get("prompt_tokens"))
    output_tokens = _int_value(usage_data.get("completion_tokens"), usage_snapshot.get("completion_tokens"))
    input_audio_tokens = _int_value(
        usage_data.get("input_audio_tokens"),
        usage_snapshot.get("input_audio_tokens"),
    )
    output_audio_tokens = _int_value(
        usage_data.get("output_audio_tokens"),
        usage_snapshot.get("output_audio_tokens"),
    )
    cached_input_tokens = _int_value(
        usage_data.get("prompt_tokens_cached"),
        usage_snapshot.get("prompt_tokens_cached"),
    )
    cached_output_tokens = _int_value(
        usage_data.get("completion_tokens_cached"),
        usage_snapshot.get("completion_tokens_cached"),
    )
    total_tokens = _int_value(
        usage_data.get("total_tokens"),
        usage_snapshot.get("total_tokens"),
        input_tokens + output_tokens + input_audio_tokens + output_audio_tokens,
    )

    return {
        "request_id": request_id,
        "call_type": call_type,
        "api_key": api_key,
        "user_id": user_id,
        "team_id": team_id,
        "organization_id": organization_id,
        "end_user_id": end_user_id,
        "model": model,
        "deployment_model": _str_or_none(meta.get("deployment_model")),
        "provider": _resolve_provider(
            explicit_provider=meta.get("provider"),
            api_base=meta.get("api_base"),
            deployment_model=meta.get("deployment_model"),
            model=model,
        ),
        "api_base": _str_or_none(meta.get("api_base")),
        "spend": float(cost),
        "provider_cost": _float_or_none(meta.get("provider_cost")),
        "billing_unit": _str_or_none(billing.get("billing_unit")),
        "pricing_tier": _str_or_none(meta.get("pricing_tier")),
        "total_tokens": total_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cached_output_tokens": cached_output_tokens,
        "input_audio_tokens": input_audio_tokens,
        "output_audio_tokens": output_audio_tokens,
        "input_characters": _int_value(usage_data.get("input_characters"), usage_snapshot.get("input_characters")),
        "output_characters": _int_value(usage_data.get("output_characters"), usage_snapshot.get("output_characters")),
        "duration_seconds": _float_value(usage_data.get("duration_seconds"), usage_snapshot.get("duration_seconds")),
        "image_count": _int_value(usage_data.get("images"), usage_snapshot.get("image_count"), usage_snapshot.get("images")),
        "rerank_units": _int_value(usage_data.get("rerank_units"), usage_snapshot.get("rerank_units")),
        "start_time": start_time,
        "end_time": end_time,
        "latency_ms": _latency_ms(start_time, end_time),
        "cache_hit": bool(cache_hit),
        "cache_key": _str_or_none(meta.get("cache_key")),
        "request_tags": [str(tag) for tag in meta.get("tags", [])] if isinstance(meta.get("tags"), list) else [],
        "unpriced_reason": _str_or_none(billing.get("unpriced_reason")),
        "pricing_fields_used": billing.get("pricing_fields_used") if isinstance(billing.get("pricing_fields_used"), list) else None,
        "usage_snapshot": usage_snapshot or None,
        "metadata": meta,
        "status": str(status or "success"),
        "http_status_code": _int_or_none(http_status_code),
        "error_type": _str_or_none(error_type),
    }


def _resolve_provider(
    *,
    explicit_provider: Any,
    api_base: Any,
    deployment_model: Any,
    model: str,
) -> str | None:
    provider = _normalized_provider(explicit_provider)
    if provider:
        return provider

    provider = _provider_from_api_base(api_base)
    if provider:
        return provider

    provider = _provider_from_model(deployment_model)
    if provider:
        return provider

    return _provider_from_model(model)


def _int_value(*values: Any) -> int:
    for value in values:
        try:
            if value is None:
                continue
            return int(value)
        except Exception:
            continue
    return 0


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _float_value(*values: Any) -> float:
    for value in values:
        try:
            if value is None:
                continue
            return float(value)
        except Exception:
            continue
    return 0.0


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _normalized_provider(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _provider_from_model(value: Any) -> str | None:
    provider = provider_from_model(str(value or ""))
    return None if provider == "unknown" else provider


def _latency_ms(start_time: datetime, end_time: datetime) -> int | None:
    try:
        return max(int((end_time - start_time).total_seconds() * 1000), 0)
    except Exception:
        return None


def _provider_from_api_base(value: Any) -> str | None:
    if value is None:
        return None
    lowered = str(value).lower()
    if "openai.azure.com" in lowered:
        return "azure_openai"
    if "api.groq.com" in lowered or "groq" in lowered:
        return "groq"
    if "openrouter.ai" in lowered or "openrouter" in lowered:
        return "openrouter"
    if "fireworks" in lowered:
        return "fireworks"
    if "together" in lowered:
        return "together"
    if "deepinfra" in lowered:
        return "deepinfra"
    if "perplexity" in lowered:
        return "perplexity"
    if "anthropic" in lowered:
        return "anthropic"
    if "googleapis.com" in lowered or "generativelanguage.googleapis.com" in lowered:
        return "gemini"
    if "bedrock" in lowered:
        return "bedrock"
    if "openai.com" in lowered or "openai" in lowered:
        return "openai"
    if "azure" in lowered:
        return "azure_openai"
    return None
