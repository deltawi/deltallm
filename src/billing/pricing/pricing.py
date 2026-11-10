from __future__ import annotations

from typing import Any, Mapping

from src.billing.pricing.cost import ModelPricing


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
    input_cost_cache_write = _float_or_none(info.get("input_cost_per_token_cache_write"))
    input_cost_cache_write_1h = _float_or_none(info.get("input_cost_per_token_cache_write_1h"))
    cost_per_request = _float_or_none(info.get("cost_per_request"))

    if input_cost is None and fallback_input_cost_per_token is not None:
        input_cost = float(fallback_input_cost_per_token)
    if output_cost is None and fallback_output_cost_per_token is not None:
        output_cost = float(fallback_output_cost_per_token)

    if (
        input_cost is None
        and output_cost is None
        and input_cost_cache_hit is None
        and output_cost_cache_hit is None
        and input_cost_cache_write is None
        and input_cost_cache_write_1h is None
        and cost_per_request is None
    ):
        return None

    return ModelPricing(
        input_cost_per_token=float(input_cost or 0.0),
        output_cost_per_token=float(output_cost or 0.0),
        input_cost_per_token_cache_hit=input_cost_cache_hit,
        output_cost_per_token_cache_hit=output_cost_cache_hit,
        cost_per_request=float(cost_per_request or 0.0),
        input_cost_per_token_cache_write=input_cost_cache_write,
        input_cost_per_token_cache_write_1h=input_cost_cache_write_1h,
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


def with_reported_prompt_cache(usage: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy usage and add the cache counts that a provider reports only in details.

    OpenAI-shaped answers (OpenAI, Azure OpenAI, vLLM, SGLang, OpenAI-compatible servers and
    translated Gemini, Bedrock and Anthropic answers) report cache reads in
    ``prompt_tokens_details.cached_tokens`` and some report cache writes in
    ``prompt_tokens_details.cache_write_tokens``. This adds them as ``prompt_tokens_cached``
    and ``prompt_tokens_cache_write``. An explicit count wins. Counts that are not whole
    numbers, or that add up to more than ``prompt_tokens``, are ignored, so those tokens
    keep the input price.
    """
    data = dict(usage or {})
    details = data.get("prompt_tokens_details")
    prompt = data.get("prompt_tokens")
    if not isinstance(details, Mapping) or not _is_count(prompt):
        return data
    if data.get("prompt_tokens_cached") is None:
        cached = details.get("cached_tokens")
        if _is_count(cached) and 0 < cached <= prompt:
            data["prompt_tokens_cached"] = cached
    if data.get("prompt_tokens_cache_write") is None:
        written = details.get("cache_write_tokens")
        cached = data.get("prompt_tokens_cached") or 0
        if _is_count(written) and _is_count(cached) and 0 < written <= prompt - cached:
            data["prompt_tokens_cache_write"] = written
    return data


def _is_count(value: object) -> bool:
    return type(value) is int and value >= 0
