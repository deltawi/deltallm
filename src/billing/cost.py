from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ModelPricing:
    input_cost_per_token: float = 0.0
    output_cost_per_token: float = 0.0
    input_cost_per_token_cache_hit: float | None = None
    output_cost_per_token_cache_hit: float | None = None
    cost_per_request: float = 0.0
    context_window: int = 8192
    max_output_tokens: int | None = None


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


def completion_cost(
    *,
    model: str,
    usage: Mapping[str, int] | None,
    cache_hit: bool = False,
    cost_map: Mapping[str, ModelPricing] | None = None,
    custom_pricing: ModelPricing | None = None,
) -> float:
    pricing = get_model_pricing(model, cost_map=cost_map, custom_pricing=custom_pricing)
    if pricing is None:
        return 0.0

    data = usage or {}
    prompt_tokens = max(0, int(data.get("prompt_tokens", 0) or 0))
    completion_tokens = max(0, int(data.get("completion_tokens", 0) or 0))
    cached_prompt_tokens = max(0, int(data.get("prompt_tokens_cached", 0) or 0))
    uncached_prompt_tokens = max(0, prompt_tokens - cached_prompt_tokens)

    if cache_hit and pricing.input_cost_per_token_cache_hit is not None:
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
    info = dict(model_info or {})

    if mode == "chat" or mode == "embedding" or mode == "rerank":
        input_cost = float(info.get("input_cost_per_token") or 0)
        output_cost = float(info.get("output_cost_per_token") or 0)
        prompt_tokens = max(0, int(usage.get("prompt_tokens", 0) or 0))
        completion_tokens = max(0, int(usage.get("completion_tokens", 0) or 0))
        return round(prompt_tokens * input_cost + completion_tokens * output_cost, 10)

    if mode == "image_generation":
        cost_per_image = float(info.get("input_cost_per_image") or 0)
        num_images = max(0, int(usage.get("images", 1) or 1))
        return round(num_images * cost_per_image, 10)

    if mode == "audio_speech":
        cost_per_char = float(info.get("input_cost_per_character") or 0)
        characters = max(0, int(usage.get("characters", 0) or 0))
        if cost_per_char > 0:
            return round(characters * cost_per_char, 10)
        cost_per_audio_token = float(info.get("input_cost_per_audio_token") or 0)
        audio_tokens = max(0, int(usage.get("audio_tokens", 0) or 0))
        return round(audio_tokens * cost_per_audio_token, 10)

    if mode == "audio_transcription":
        cost_per_second = float(info.get("input_cost_per_second") or 0)
        duration = max(0, float(usage.get("duration_seconds", 0) or 0))
        return round(duration * cost_per_second, 10)

    return 0.0
