from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


@dataclass(frozen=True)
class ModelPricing:
    input_cost_per_token: float = 0.0
    output_cost_per_token: float = 0.0
    input_cost_per_token_cache_hit: float | None = None
    output_cost_per_token_cache_hit: float | None = None
    cost_per_request: float = 0.0
    context_window: int = 8192
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class BillingResult:
    cost: float
    billing_unit: str | None = None
    pricing_fields_used: tuple[str, ...] = ()
    missing_pricing_fields: tuple[str, ...] = ()
    usage_snapshot: dict[str, float | int] = field(default_factory=dict)
    unpriced_reason: str | None = None


DEFAULT_MODEL_COST_MAP: dict[str, ModelPricing] = {
    "gpt-4": ModelPricing(input_cost_per_token=0.00003, output_cost_per_token=0.00006, context_window=8192),
    "gpt-4-32k": ModelPricing(input_cost_per_token=0.00006, output_cost_per_token=0.00012, context_window=32768),
    "gpt-4-turbo": ModelPricing(
        input_cost_per_token=0.00001,
        output_cost_per_token=0.00003,
        input_cost_per_token_cache_hit=0.000005,
        context_window=128000,
    ),
    "gpt-4o": ModelPricing(
        input_cost_per_token=0.000005,
        output_cost_per_token=0.000015,
        input_cost_per_token_cache_hit=0.0000025,
        context_window=128000,
    ),
    "gpt-4o-mini": ModelPricing(
        input_cost_per_token=0.00000015,
        output_cost_per_token=0.0000006,
        input_cost_per_token_cache_hit=0.000000075,
        context_window=128000,
    ),
    "gpt-3.5-turbo": ModelPricing(input_cost_per_token=0.0000005, output_cost_per_token=0.0000015, context_window=16385),
    "text-embedding-3-small": ModelPricing(input_cost_per_token=0.00000002, output_cost_per_token=0.0, context_window=8192),
    "text-embedding-3-large": ModelPricing(input_cost_per_token=0.00000013, output_cost_per_token=0.0, context_window=8192),
    "claude-3-opus": ModelPricing(input_cost_per_token=0.000015, output_cost_per_token=0.000075, context_window=200000),
    "claude-3-sonnet": ModelPricing(input_cost_per_token=0.000003, output_cost_per_token=0.000015, context_window=200000),
    "claude-3-haiku": ModelPricing(input_cost_per_token=0.00000025, output_cost_per_token=0.00000125, context_window=200000),
}


def get_model_pricing(
    model: str,
    *,
    cost_map: Mapping[str, ModelPricing] | None = None,
    custom_pricing: ModelPricing | None = None,
) -> ModelPricing | None:
    if custom_pricing is not None:
        return custom_pricing

    pricing_map = dict(cost_map or DEFAULT_MODEL_COST_MAP)
    if model in pricing_map:
        return pricing_map[model]

    for prefix in sorted(pricing_map.keys(), key=len, reverse=True):
        if model.startswith(prefix):
            return pricing_map[prefix]

    return None


def resolve_batch_pricing(
    *,
    sync_pricing: ModelPricing | None,
    model_info: Mapping[str, Any] | None = None,
) -> ModelPricing | None:
    info = dict(model_info or {})
    batch_input = info.get("batch_input_cost_per_token")
    batch_output = info.get("batch_output_cost_per_token")
    multiplier = info.get("batch_price_multiplier")

    if batch_input is not None or batch_output is not None:
        return ModelPricing(
            input_cost_per_token=float(
                batch_input
                if batch_input is not None
                else (sync_pricing.input_cost_per_token if sync_pricing is not None else 0.0)
            ),
            output_cost_per_token=float(
                batch_output
                if batch_output is not None
                else (sync_pricing.output_cost_per_token if sync_pricing is not None else 0.0)
            ),
            input_cost_per_token_cache_hit=(
                sync_pricing.input_cost_per_token_cache_hit if sync_pricing is not None else None
            ),
            output_cost_per_token_cache_hit=(
                sync_pricing.output_cost_per_token_cache_hit if sync_pricing is not None else None
            ),
            cost_per_request=sync_pricing.cost_per_request if sync_pricing is not None else 0.0,
            context_window=sync_pricing.context_window if sync_pricing is not None else 8192,
            max_output_tokens=sync_pricing.max_output_tokens if sync_pricing is not None else None,
        )

    if sync_pricing is None:
        return None

    if multiplier is not None:
        factor = max(0.0, float(multiplier))
        return ModelPricing(
            input_cost_per_token=sync_pricing.input_cost_per_token * factor,
            output_cost_per_token=sync_pricing.output_cost_per_token * factor,
            input_cost_per_token_cache_hit=(
                sync_pricing.input_cost_per_token_cache_hit * factor
                if sync_pricing.input_cost_per_token_cache_hit is not None
                else None
            ),
            output_cost_per_token_cache_hit=(
                sync_pricing.output_cost_per_token_cache_hit * factor
                if sync_pricing.output_cost_per_token_cache_hit is not None
                else None
            ),
            cost_per_request=sync_pricing.cost_per_request * factor,
            context_window=sync_pricing.context_window,
            max_output_tokens=sync_pricing.max_output_tokens,
        )

    return sync_pricing


def completion_cost(
    *,
    model: str,
    usage: Mapping[str, int] | None,
    cache_hit: bool = False,
    cost_map: Mapping[str, ModelPricing] | None = None,
    custom_pricing: ModelPricing | None = None,
    pricing_tier: Literal["sync", "batch"] = "sync",
    model_info: Mapping[str, Any] | None = None,
) -> float:
    pricing = get_model_pricing(model, cost_map=cost_map, custom_pricing=custom_pricing)
    if pricing_tier == "batch":
        pricing = resolve_batch_pricing(sync_pricing=pricing, model_info=model_info)
    if pricing is None:
        return 0.0

    data = usage or {}
    prompt_tokens = max(0, int(data.get("prompt_tokens", 0) or 0))
    completion_tokens = max(0, int(data.get("completion_tokens", 0) or 0))
    cached_prompt_tokens = max(0, int(data.get("prompt_tokens_cached", 0) or 0))
    uncached_prompt_tokens = max(0, prompt_tokens - cached_prompt_tokens)

    if pricing.input_cost_per_token_cache_hit is not None and (cache_hit or cached_prompt_tokens > 0):
        prompt_cost = (
            cached_prompt_tokens * pricing.input_cost_per_token_cache_hit
            + uncached_prompt_tokens * pricing.input_cost_per_token
        )
    else:
        prompt_cost = prompt_tokens * pricing.input_cost_per_token

    if cache_hit and pricing.output_cost_per_token_cache_hit is not None:
        output_cost = completion_tokens * pricing.output_cost_per_token_cache_hit
    else:
        output_cost = completion_tokens * pricing.output_cost_per_token

    total_cost = prompt_cost + output_cost + pricing.cost_per_request
    return round(total_cost, 10)


def compute_cost(
    *,
    mode: str,
    usage: Mapping[str, Any],
    model_info: Mapping[str, Any] | None = None,
) -> float:
    return compute_billing_result(mode=mode, usage=usage, model_info=model_info).cost


def compute_billing_result(
    *,
    mode: str,
    usage: Mapping[str, Any],
    model_info: Mapping[str, Any] | None = None,
) -> BillingResult:
    info = dict(model_info or {})
    cost_per_request = _cost_per_request(info)

    if mode == "chat" or mode == "embedding" or mode == "rerank":
        input_cost = _float_or_zero(info.get("input_cost_per_token"))
        output_cost = _float_or_zero(info.get("output_cost_per_token"))
        prompt_tokens = max(0, int(usage.get("prompt_tokens", 0) or 0))
        completion_tokens = max(0, int(usage.get("completion_tokens", 0) or 0))
        pricing_fields = _usage_pricing_fields(
            info,
            (prompt_tokens, "input_cost_per_token"),
            (completion_tokens, "output_cost_per_token"),
        )
        return BillingResult(
            cost=_rounded_cost(prompt_tokens * input_cost + completion_tokens * output_cost + cost_per_request),
            billing_unit="request" if pricing_fields == ("cost_per_request",) else "token",
            pricing_fields_used=pricing_fields,
            usage_snapshot={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        )

    if mode == "image_generation":
        input_price_configured = _price_is_configured(
            info,
            "input_cost_per_image",
        )
        output_price_configured = _price_is_configured(
            info,
            "output_cost_per_image",
        )
        request_price_configured = _price_is_configured(info, "cost_per_request")
        output_price_field = (
            "output_cost_per_image"
            if output_price_configured
            else "input_cost_per_image"
        )
        output_cost_per_image = _float_or_zero(info.get(output_price_field))
        input_cost_per_image = _float_or_zero(info.get("input_cost_per_image"))
        output_images = max(
            0,
            int(
                usage.get(
                    "output_images",
                    usage.get("images", 1),
                )
                or 0
            ),
        )
        input_images = max(0, int(usage.get("input_images", 0) or 0))
        usage_snapshot = {
            "images": output_images,
            "output_images": output_images,
            "input_images": input_images,
        }
        image_price_configured = input_price_configured or output_price_configured
        if not image_price_configured and request_price_configured:
            return BillingResult(
                cost=_rounded_cost(cost_per_request),
                billing_unit="request",
                pricing_fields_used=("cost_per_request",),
                usage_snapshot=usage_snapshot,
            )
        missing_pricing_fields: list[str] = []
        if output_images > 0 and not image_price_configured:
            missing_pricing_fields.append("output_cost_per_image")
        if input_images > 0 and not input_price_configured:
            missing_pricing_fields.append("input_cost_per_image")
        if missing_pricing_fields:
            return BillingResult(
                cost=0.0,
                pricing_fields_used=(
                    ("cost_per_request",) if request_price_configured else ()
                ),
                missing_pricing_fields=tuple(missing_pricing_fields),
                usage_snapshot=usage_snapshot,
                unpriced_reason="no_configured_pricing",
            )
        pricing_fields = _usage_pricing_fields(
            info,
            (output_images, output_price_field),
            (
                input_images if output_price_field != "input_cost_per_image" else 0,
                "input_cost_per_image",
            ),
        )
        return BillingResult(
            cost=_rounded_cost(
                output_images * output_cost_per_image
                + input_images * input_cost_per_image
                + cost_per_request
            ),
            billing_unit=(
                "request"
                if pricing_fields == ("cost_per_request",)
                else "image"
            ),
            pricing_fields_used=pricing_fields,
            usage_snapshot=usage_snapshot,
        )

    if mode == "audio_speech":
        return _compute_audio_speech_billing(usage=usage, model_info=info)

    if mode == "audio_transcription":
        return _compute_audio_transcription_billing(usage=usage, model_info=info)

    return BillingResult(cost=0.0, unpriced_reason=f"unsupported_mode:{mode}")


def _compute_audio_speech_billing(
    *,
    usage: Mapping[str, Any],
    model_info: Mapping[str, Any],
) -> BillingResult:
    cost_per_request = _cost_per_request(model_info)
    prompt_tokens = max(0, int(usage.get("prompt_tokens", 0) or 0))
    completion_tokens = max(0, int(usage.get("completion_tokens", 0) or 0))
    input_audio_tokens = max(0, int(usage.get("input_audio_tokens", usage.get("audio_tokens", 0)) or 0))
    output_audio_tokens = max(0, int(usage.get("output_audio_tokens", 0) or 0))

    input_cost_per_token = _float_or_zero(model_info.get("input_cost_per_token"))
    output_cost_per_token = _float_or_zero(model_info.get("output_cost_per_token"))
    input_cost_per_audio_token = _float_or_zero(model_info.get("input_cost_per_audio_token"))
    output_cost_per_audio_token = _float_or_zero(model_info.get("output_cost_per_audio_token"))

    has_token_usage = any(
        value > 0 for value in (prompt_tokens, completion_tokens, input_audio_tokens, output_audio_tokens)
    )
    token_usage_fields = (
        (prompt_tokens, "input_cost_per_token"),
        (completion_tokens, "output_cost_per_token"),
        (input_audio_tokens, "input_cost_per_audio_token"),
        (output_audio_tokens, "output_cost_per_audio_token"),
    )
    has_token_pricing = any(
        usage_value > 0 and _price_is_configured(model_info, pricing_field)
        for usage_value, pricing_field in token_usage_fields
    )
    if has_token_usage and has_token_pricing:
        missing_pricing_fields = _missing_usage_pricing_fields(
            model_info,
            *token_usage_fields,
        )
        if missing_pricing_fields:
            return BillingResult(
                cost=0.0,
                pricing_fields_used=_usage_pricing_fields(
                    model_info,
                    *token_usage_fields,
                ),
                missing_pricing_fields=missing_pricing_fields,
                usage_snapshot={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "input_audio_tokens": input_audio_tokens,
                    "output_audio_tokens": output_audio_tokens,
                },
                unpriced_reason="no_configured_pricing",
            )
        cost = (
            prompt_tokens * input_cost_per_token
            + completion_tokens * output_cost_per_token
            + input_audio_tokens * input_cost_per_audio_token
            + output_audio_tokens * output_cost_per_audio_token
            + cost_per_request
        )
        return BillingResult(
            cost=_rounded_cost(cost),
            billing_unit="token",
            pricing_fields_used=_usage_pricing_fields(
                model_info,
                (prompt_tokens, "input_cost_per_token"),
                (completion_tokens, "output_cost_per_token"),
                (input_audio_tokens, "input_cost_per_audio_token"),
                (output_audio_tokens, "output_cost_per_audio_token"),
            ),
            usage_snapshot={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "input_audio_tokens": input_audio_tokens,
                "output_audio_tokens": output_audio_tokens,
            },
        )

    input_characters = max(0, int(usage.get("input_characters", usage.get("characters", 0)) or 0))
    output_characters = max(0, int(usage.get("output_characters", 0) or 0))
    input_cost_per_character = _float_or_zero(model_info.get("input_cost_per_character"))
    output_cost_per_character = _float_or_zero(model_info.get("output_cost_per_character"))
    has_character_usage = input_characters > 0 or output_characters > 0
    character_usage_fields = (
        (input_characters, "input_cost_per_character"),
        (output_characters, "output_cost_per_character"),
    )
    has_character_pricing = any(
        usage_value > 0 and _price_is_configured(model_info, pricing_field)
        for usage_value, pricing_field in character_usage_fields
    )
    if has_character_usage and has_character_pricing:
        missing_pricing_fields = _missing_usage_pricing_fields(
            model_info,
            *character_usage_fields,
        )
        if missing_pricing_fields:
            return BillingResult(
                cost=0.0,
                pricing_fields_used=_usage_pricing_fields(
                    model_info,
                    *character_usage_fields,
                ),
                missing_pricing_fields=missing_pricing_fields,
                usage_snapshot={
                    "input_characters": input_characters,
                    "output_characters": output_characters,
                },
                unpriced_reason="no_configured_pricing",
            )
        cost = (
            input_characters * input_cost_per_character
            + output_characters * output_cost_per_character
            + cost_per_request
        )
        return BillingResult(
            cost=_rounded_cost(cost),
            billing_unit="character",
            pricing_fields_used=_usage_pricing_fields(
                model_info,
                (input_characters, "input_cost_per_character"),
                (output_characters, "output_cost_per_character"),
            ),
            usage_snapshot={
                "input_characters": input_characters,
                "output_characters": output_characters,
            },
        )

    duration_seconds = max(0.0, float(usage.get("duration_seconds", 0) or 0))
    input_cost_per_second = _float_or_zero(model_info.get("input_cost_per_second"))
    output_cost_per_second = _float_or_zero(model_info.get("output_cost_per_second"))
    has_second_pricing = any(
        _price_is_configured(model_info, field)
        for field in ("input_cost_per_second", "output_cost_per_second")
    )
    if duration_seconds > 0 and has_second_pricing:
        cost = duration_seconds * (input_cost_per_second + output_cost_per_second) + cost_per_request
        return BillingResult(
            cost=_rounded_cost(cost),
            billing_unit="second",
            pricing_fields_used=_usage_pricing_fields(
                model_info,
                (duration_seconds, "input_cost_per_second"),
                (duration_seconds, "output_cost_per_second"),
            ),
            usage_snapshot={"duration_seconds": duration_seconds},
        )

    if _price_is_configured(model_info, "cost_per_request"):
        return BillingResult(
            cost=_rounded_cost(cost_per_request),
            billing_unit="request",
            pricing_fields_used=("cost_per_request",),
            usage_snapshot=_compact_usage_snapshot(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                input_audio_tokens=input_audio_tokens,
                output_audio_tokens=output_audio_tokens,
                input_characters=input_characters,
                output_characters=output_characters,
                duration_seconds=duration_seconds,
            ),
        )

    return BillingResult(
        cost=0.0,
        unpriced_reason="missing_tts_pricing_or_usage",
        usage_snapshot=_compact_usage_snapshot(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            input_audio_tokens=input_audio_tokens,
            output_audio_tokens=output_audio_tokens,
            input_characters=input_characters,
            output_characters=output_characters,
            duration_seconds=duration_seconds,
        ),
    )


def _compute_audio_transcription_billing(
    *,
    usage: Mapping[str, Any],
    model_info: Mapping[str, Any],
) -> BillingResult:
    cost_per_request = _cost_per_request(model_info)
    prompt_tokens = max(0, int(usage.get("prompt_tokens", 0) or 0))
    completion_tokens = max(0, int(usage.get("completion_tokens", 0) or 0))
    input_audio_tokens = max(0, int(usage.get("input_audio_tokens", usage.get("audio_tokens", 0)) or 0))

    input_cost_per_token = _float_or_zero(model_info.get("input_cost_per_token"))
    output_cost_per_token = _float_or_zero(model_info.get("output_cost_per_token"))
    input_cost_per_audio_token = _float_or_zero(model_info.get("input_cost_per_audio_token"))
    has_token_usage = any(value > 0 for value in (prompt_tokens, completion_tokens, input_audio_tokens))
    token_usage_fields = (
        (prompt_tokens, "input_cost_per_token"),
        (completion_tokens, "output_cost_per_token"),
        (input_audio_tokens, "input_cost_per_audio_token"),
    )
    has_token_pricing = any(
        usage_value > 0 and _price_is_configured(model_info, pricing_field)
        for usage_value, pricing_field in token_usage_fields
    )
    if has_token_usage and has_token_pricing:
        missing_pricing_fields = _missing_usage_pricing_fields(
            model_info,
            *token_usage_fields,
        )
        if missing_pricing_fields:
            return BillingResult(
                cost=0.0,
                pricing_fields_used=_usage_pricing_fields(
                    model_info,
                    *token_usage_fields,
                ),
                missing_pricing_fields=missing_pricing_fields,
                usage_snapshot={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "input_audio_tokens": input_audio_tokens,
                },
                unpriced_reason="no_configured_pricing",
            )
        cost = (
            prompt_tokens * input_cost_per_token
            + completion_tokens * output_cost_per_token
            + input_audio_tokens * input_cost_per_audio_token
            + cost_per_request
        )
        return BillingResult(
            cost=_rounded_cost(cost),
            billing_unit="token",
            pricing_fields_used=_usage_pricing_fields(
                model_info,
                (prompt_tokens, "input_cost_per_token"),
                (completion_tokens, "output_cost_per_token"),
                (input_audio_tokens, "input_cost_per_audio_token"),
            ),
            usage_snapshot={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "input_audio_tokens": input_audio_tokens,
            },
        )

    raw_duration_seconds = max(0.0, float(usage.get("duration_seconds", 0) or 0))
    duration_seconds = max(0.0, float(usage.get("billable_duration_seconds", raw_duration_seconds) or 0))
    input_cost_per_second = _float_or_zero(model_info.get("input_cost_per_second"))
    output_cost_per_second = _float_or_zero(model_info.get("output_cost_per_second"))
    has_second_pricing = any(
        _price_is_configured(model_info, field)
        for field in ("input_cost_per_second", "output_cost_per_second")
    )
    if duration_seconds > 0 and has_second_pricing:
        cost = duration_seconds * (input_cost_per_second + output_cost_per_second) + cost_per_request
        usage_snapshot: dict[str, float | int] = {"duration_seconds": raw_duration_seconds or duration_seconds}
        if raw_duration_seconds > 0 and duration_seconds != raw_duration_seconds:
            usage_snapshot["billable_duration_seconds"] = duration_seconds
        return BillingResult(
            cost=_rounded_cost(cost),
            billing_unit="second",
            pricing_fields_used=_usage_pricing_fields(
                model_info,
                (duration_seconds, "input_cost_per_second"),
                (duration_seconds, "output_cost_per_second"),
            ),
            usage_snapshot=usage_snapshot,
        )

    if _price_is_configured(model_info, "cost_per_request"):
        return BillingResult(
            cost=_rounded_cost(cost_per_request),
            billing_unit="request",
            pricing_fields_used=("cost_per_request",),
            usage_snapshot=_compact_usage_snapshot(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                input_audio_tokens=input_audio_tokens,
                duration_seconds=raw_duration_seconds or duration_seconds,
            ),
        )

    return BillingResult(
        cost=0.0,
        unpriced_reason="missing_stt_pricing_or_usage",
        usage_snapshot=_compact_usage_snapshot(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            input_audio_tokens=input_audio_tokens,
            duration_seconds=raw_duration_seconds or duration_seconds,
        ),
    )


def _compact_usage_snapshot(**values: float | int) -> dict[str, float | int]:
    return {key: value for key, value in values.items() if value}


def _usage_pricing_fields(
    model_info: Mapping[str, Any],
    *usage_fields: tuple[float | int, str],
) -> tuple[str, ...]:
    fields = [
        field_name
        for usage_value, field_name in usage_fields
        if usage_value > 0 and _price_is_configured(model_info, field_name)
    ]
    if _price_is_configured(model_info, "cost_per_request"):
        fields.append("cost_per_request")
    return tuple(dict.fromkeys(fields))


def _missing_usage_pricing_fields(
    model_info: Mapping[str, Any],
    *usage_fields: tuple[float | int, str],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            field_name
            for usage_value, field_name in usage_fields
            if usage_value > 0 and not _price_is_configured(model_info, field_name)
        )
    )


def _rounded_cost(value: float) -> float:
    return round(float(value), 10)


def _cost_per_request(model_info: Mapping[str, Any]) -> float:
    return _float_or_zero(model_info.get("cost_per_request"))


def _price_is_configured(model_info: Mapping[str, Any], field: str) -> bool:
    return field in model_info and model_info.get(field) is not None


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
