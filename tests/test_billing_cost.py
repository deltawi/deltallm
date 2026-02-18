from __future__ import annotations

from src.billing.cost import ModelPricing, completion_cost, get_model_pricing


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


def test_get_model_pricing_prefix_match() -> None:
    pricing = get_model_pricing("gpt-4o-2024-08-06")
    assert pricing is not None
    assert pricing.input_cost_per_token > 0


def test_completion_cost_unknown_model_returns_zero() -> None:
    assert completion_cost(model="unknown-model", usage={"prompt_tokens": 10, "completion_tokens": 1}) == 0.0
