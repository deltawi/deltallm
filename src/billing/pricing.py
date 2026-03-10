from __future__ import annotations

from typing import Any, Mapping

from src.billing.cost import ModelPricing


def pricing_from_model_info(
    model_info: Mapping[str, Any] | None,
    *,
    fallback_input_cost_per_token: float | None = None,
    fallback_output_cost_per_token: float | None = None,
) -> ModelPricing | None:
    info = dict(model_info or {})
    input_cost = _float_or_none(info.get("input_cost_per_token"))
    output_cost = _float_or_none(info.get("output_cost_per_token"))
    input_cost_cache_hit = _float_or_none(info.get("input_cost_per_token_cache_hit"))
    output_cost_cache_hit = _float_or_none(info.get("output_cost_per_token_cache_hit"))

    if input_cost is None and fallback_input_cost_per_token is not None:
        input_cost = float(fallback_input_cost_per_token)
    if output_cost is None and fallback_output_cost_per_token is not None:
        output_cost = float(fallback_output_cost_per_token)

    if (
        input_cost is None
        and output_cost is None
        and input_cost_cache_hit is None
        and output_cost_cache_hit is None
    ):
        return None

    return ModelPricing(
        input_cost_per_token=float(input_cost or 0.0),
        output_cost_per_token=float(output_cost or 0.0),
        input_cost_per_token_cache_hit=input_cost_cache_hit,
        output_cost_per_token_cache_hit=output_cost_cache_hit,
    )


def normalize_gateway_cache_hit_usage(usage: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = dict(usage or {})
    if normalized.get("prompt_tokens_cached") is None:
        prompt_tokens = max(0, int(normalized.get("prompt_tokens", 0) or 0))
        normalized["prompt_tokens_cached"] = prompt_tokens
    return normalized


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
