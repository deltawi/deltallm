from __future__ import annotations

from src.billing.cost import ModelPricing, completion_cost, get_model_pricing
from src.billing.pricing import normalize_gateway_cache_hit_usage, pricing_from_model_info


def test_completion_cost_uses_default_pricing() -> None:
    cost = completion_cost(
        model="gpt-4o-mini",
        usage={"prompt_tokens": 1000, "completion_tokens": 500},
    )
    assert cost == 0.00045


def test_completion_cost_uses_cache_hit_pricing() -> None:
    pricing = ModelPricing(
        input_cost_per_token=1.0,
        output_cost_per_token=2.0,
        input_cost_per_token_cache_hit=0.5,
    )
    cost = completion_cost(
        model="custom-model",
        usage={"prompt_tokens": 10, "prompt_tokens_cached": 4, "completion_tokens": 3},
        cache_hit=True,
        custom_pricing=pricing,
    )
    assert cost == 14.0


def test_completion_cost_uses_cached_prompt_token_pricing_without_gateway_cache_hit() -> None:
    pricing = ModelPricing(
        input_cost_per_token=1.0,
        output_cost_per_token=2.0,
        input_cost_per_token_cache_hit=0.5,
    )
    cost = completion_cost(
        model="custom-model",
        usage={"prompt_tokens": 10, "prompt_tokens_cached": 4, "completion_tokens": 3},
        custom_pricing=pricing,
    )
    assert cost == 14.0


def test_get_model_pricing_prefix_match() -> None:
    pricing = get_model_pricing("gpt-4o-2024-08-06")
    assert pricing is not None
    assert pricing.input_cost_per_token > 0


def test_completion_cost_unknown_model_returns_zero() -> None:
    assert completion_cost(model="unknown-model", usage={"prompt_tokens": 10, "completion_tokens": 1}) == 0.0


def test_batch_cost_uses_batch_absolute_pricing_over_sync() -> None:
    pricing = ModelPricing(input_cost_per_token=2.0, output_cost_per_token=3.0)
    cost = completion_cost(
        model="custom-model",
        usage={"prompt_tokens": 5, "completion_tokens": 2},
        custom_pricing=pricing,
        pricing_tier="batch",
        model_info={
            "batch_input_cost_per_token": 1.0,
            "batch_output_cost_per_token": 1.5,
            "batch_price_multiplier": 0.2,
        },
    )
    assert cost == 8.0


def test_batch_cost_uses_multiplier_when_absolute_missing() -> None:
    pricing = ModelPricing(input_cost_per_token=2.0, output_cost_per_token=1.0)
    cost = completion_cost(
        model="custom-model",
        usage={"prompt_tokens": 10, "completion_tokens": 4},
        custom_pricing=pricing,
        pricing_tier="batch",
        model_info={"batch_price_multiplier": 0.5},
    )
    assert cost == 12.0


def test_normalize_gateway_cache_hit_usage_marks_full_prompt_cached() -> None:
    usage = normalize_gateway_cache_hit_usage({"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})
    assert usage["prompt_tokens_cached"] == 10


def test_pricing_from_model_info_includes_cache_hit_fields() -> None:
    pricing = pricing_from_model_info(
        {
            "input_cost_per_token": 1.0,
            "output_cost_per_token": 2.0,
            "input_cost_per_token_cache_hit": 0.25,
            "output_cost_per_token_cache_hit": 0.5,
        }
    )
    assert pricing is not None
    assert pricing.input_cost_per_token_cache_hit == 0.25
    assert pricing.output_cost_per_token_cache_hit == 0.5
