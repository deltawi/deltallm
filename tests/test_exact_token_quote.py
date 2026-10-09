from __future__ import annotations

import ast
from dataclasses import replace
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from src.billing.cost import ModelPricing
from src.billing.tier_pricing import (
    PricingResolution,
    resolve_exact_token_quote_pricing,
    resolve_token_quote_pricing,
)
from src.billing.token_quote_policy import ExactTokenRates


def _resolution(info: dict[str, object], *, mode="batch") -> PricingResolution:
    return PricingResolution(
        callable_model="test-model",
        provider_model="test-model",
        requested_mode=mode,
        source="deployment",
        customer_model_info=dict(info),
        provider_model_info=dict(info),
        customer_token_pricing=None,
        provider_token_pricing=None,
        provider_pricing_fields=tuple(sorted(info)),
        catalog_pricing_frozen=True,
    )


def test_exact_quote_keeps_sub_display_precision_and_decimal_multiplier():
    resolution = _resolution(
        {
            "input_cost_per_token": "0.000000000000000003",
            "output_cost_per_token": "0.000000000000000007",
            "batch_price_multiplier": "0.1",
            "cost_per_request": "0.000000000000000005",
        }
    )
    with localcontext() as context:
        context.prec = 6
        quote = resolve_exact_token_quote_pricing(
            resolution,
            model="test-model",
            prompt_tokens=101,
            completion_tokens=7,
        )
        assert quote.pricing is not None
        assert quote.pricing.cost(prompt_tokens=101, completion_tokens=7) == Decimal(
            "0.000000000000000036"
        )
        assert quote.pricing.input_cost_per_token == Decimal("0.0000000000000000003")
    assert quote.pricing_fields_used == (
        "cost_per_request",
        "batch_price_multiplier",
        "input_cost_per_token",
        "output_cost_per_token",
    )
    assert quote.pricing_sources_used == ("deployment",)


@pytest.mark.parametrize("cache_hit,cached", [(False, 0), (False, 3), (True, 0), (True, 4)])
@pytest.mark.parametrize("mode", ["sync", "batch"])
@pytest.mark.parametrize("batch_fields", [False, True])
def test_legacy_facade_uses_same_rates_and_metadata(cache_hit, cached, mode, batch_fields):
    info = {
        "input_cost_per_token": "0.1",
        "output_cost_per_token": "0.2",
        "input_cost_per_token_cache_hit": "0.01",
        "output_cost_per_token_cache_hit": "0.02",
        "batch_price_multiplier": "0.3",
        "cost_per_request": "0.07",
    }
    if batch_fields:
        info.update(batch_input_cost_per_token="0.04", batch_output_cost_per_token="0.05")
    resolution = _resolution(info, mode=mode)
    kwargs = dict(
        model="test-model",
        prompt_tokens=10,
        completion_tokens=5,
        prompt_tokens_cached=cached,
        cache_hit=cache_hit,
    )
    exact = resolve_exact_token_quote_pricing(resolution, **kwargs)
    legacy = resolve_token_quote_pricing(resolution, **kwargs)
    assert exact.pricing is not None
    assert legacy.pricing == exact.pricing.legacy_pricing()
    assert legacy.pricing_fields_used == exact.pricing_fields_used
    assert legacy.pricing_sources_used == exact.pricing_sources_used
    assert legacy.missing_pricing_fields == exact.missing_pricing_fields
    assert legacy.request_only == exact.request_only
    assert legacy.unpriced_reason == exact.unpriced_reason
    scaled = mode == "batch" and not batch_fields
    regular_input = Decimal("0.04" if mode == "batch" and batch_fields else "0.1")
    regular_output = Decimal("0.05" if mode == "batch" and batch_fields else "0.2")
    factor = Decimal("0.3") if scaled else Decimal(1)
    effective_cached = 10 if cache_hit and cached == 0 else cached
    expected = (
        (10 - effective_cached) * regular_input * factor
        + effective_cached * Decimal("0.01")
        + 5 * (Decimal("0.02") if cache_hit else regular_output * factor)
        + Decimal("0.07") * factor
    )
    assert (
        exact.pricing.cost(
            prompt_tokens=10,
            completion_tokens=5,
            prompt_tokens_cached=cached,
            cache_hit=cache_hit,
        )
        == expected
    )


def test_exact_customer_and_provider_keep_distinct_frozen_sources():
    resolution = replace(
        _resolution({"input_cost_per_token": "0.2", "output_cost_per_token": "0.4"}),
        customer_model_info={"input_cost_per_token": "0.5", "output_cost_per_token": "0.6"},
        tier_pricing_applied=True,
        tier_pricing_fields=("input_cost_per_token",),
    )
    quotes = [
        resolve_exact_token_quote_pricing(
            resolution,
            model="test-model",
            prompt_tokens=2,
            completion_tokens=3,
            pricing_view=view,
        )
        for view in ("customer", "provider")
    ]
    customer, provider = quotes
    assert customer.pricing_sources_used == ("deployment", "tier")
    assert provider.pricing_sources_used == ("deployment",)
    assert customer.pricing.cost(prompt_tokens=2, completion_tokens=3) == Decimal("2.8")
    assert provider.pricing.cost(prompt_tokens=2, completion_tokens=3) == Decimal("1.6")


def test_frozen_catalog_never_reads_changed_catalog(monkeypatch):
    import src.billing.tier_pricing as tier_pricing

    monkeypatch.setattr(tier_pricing, "get_model_pricing", lambda model: pytest.fail(model))
    resolution = replace(
        _resolution({}),
        catalog_token_pricing=ModelPricing(input_cost_per_token=0.1, output_cost_per_token=0.2),
    )
    quote = resolve_exact_token_quote_pricing(
        resolution,
        model="test-model",
        prompt_tokens=2,
        completion_tokens=3,
    )
    assert quote.pricing.cost(prompt_tokens=2, completion_tokens=3) == Decimal("0.8")
    assert quote.pricing_sources_used == ("default",)


@pytest.mark.parametrize("usage", [(0, 0), (4, 5)])
def test_request_only_batch_quote_is_exact(usage):
    quote = resolve_exact_token_quote_pricing(
        _resolution({"cost_per_request": "0.07", "batch_price_multiplier": "0.3"}),
        model="test-model",
        prompt_tokens=usage[0],
        completion_tokens=usage[1],
    )
    assert quote.request_only
    assert quote.pricing.cost(prompt_tokens=usage[0], completion_tokens=usage[1]) == Decimal(
        "0.021"
    )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "invalid"])
def test_invalid_configured_rate_is_not_free_or_catalog_fallback(value):
    resolution = replace(
        _resolution({"input_cost_per_token": value}),
        catalog_token_pricing=ModelPricing(input_cost_per_token=0.1),
    )
    quote = resolve_exact_token_quote_pricing(
        resolution,
        model="test-model",
        prompt_tokens=2,
        completion_tokens=0,
    )
    assert quote.pricing is None
    assert quote.unpriced_reason == "invalid_configured_pricing"


@pytest.mark.parametrize("counts", [(-1, 0, 0), (2, 0, 3), (True, 0, 0), (2**31, 0, 0)])
def test_exact_receipt_rejects_invalid_token_counts(counts):
    with pytest.raises(ValueError, match="Invalid exact token receipt"):
        ExactTokenRates().cost(
            prompt_tokens=counts[0],
            completion_tokens=counts[1],
            prompt_tokens_cached=counts[2],
        )


def test_shared_quote_policy_has_one_bounded_typed_owner():
    path = Path("src/billing/token_quote_policy.py")
    tree = ast.parse(path.read_text())
    assert len(path.read_text().splitlines()) < 500
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.end_lineno - node.lineno + 1 <= 80, node.name
        if isinstance(node, ast.Name):
            assert node.id not in {"Any", "getattr", "hasattr"}
    source = Path("src/billing/tier_pricing.py").read_text()
    assert "def select_regular_rate(" not in source
    assert "def select_cache_rate(" not in source
