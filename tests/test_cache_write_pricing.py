from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from src.billing.accounting.accounting_pricing import accounting_pricing_snapshot
from src.billing.charges.provider_allowance import (
    ProviderRequestBounds,
    conservative_provider_allowance,
)
from src.billing.pricing.cost import ModelPricing, completion_cost
from src.billing.pricing.pricing import pricing_from_model_info
from src.billing.pricing.tier_pricing import (
    PricingResolution,
    resolve_exact_token_quote_pricing,
    resolve_token_billing_result,
)
from src.billing.pricing.token_quote_policy import ExactTokenRates, PromptTokenSplit
from src.providers.anthropic_messages import native_usage
from src.providers.chat_usage import CompatibleChatUsage
from src.router.selection.qualification import selector_price_snapshot

# Claude Sonnet 4.6 list prices, per token.
SONNET = {
    "input_cost_per_token": "0.000003",
    "output_cost_per_token": "0.000015",
    "input_cost_per_token_cache_hit": "0.0000003",
    "input_cost_per_token_cache_write": "0.00000375",
    "input_cost_per_token_cache_write_1h": "0.000006",
}
# 2,000 new prompt tokens, 30,000 written to the cache, 70,000 read from it.
TURN = {
    "prompt_tokens": 102_000,
    "completion_tokens": 1_000,
    "total_tokens": 103_000,
    "prompt_tokens_cached": 70_000,
    "prompt_tokens_cache_write": 30_000,
}


def _resolution(info: dict[str, object], *, mode: str = "sync") -> PricingResolution:
    return PricingResolution(
        callable_model="claude-sonnet",
        provider_model="claude-sonnet",
        requested_mode=mode,
        source="deployment",
        customer_model_info=dict(info),
        provider_model_info=dict(info),
        customer_token_pricing=pricing_from_model_info(info),
        provider_token_pricing=pricing_from_model_info(info),
        provider_pricing_fields=tuple(sorted(info)),
        catalog_pricing_frozen=True,
    )


def _charge(info: dict[str, object], usage: dict[str, int], *, cache_hit: bool = False):
    return resolve_token_billing_result(
        _resolution(info), model="claude-sonnet", usage=usage, cache_hit=cache_hit
    ).billing


def test_cache_writes_use_the_cache_write_price():
    billing = _charge(SONNET, TURN)

    # 2,000 x $3/M + 30,000 x $3.75/M + 70,000 x $0.30/M + 1,000 x $15/M
    assert billing.exact_cost == Decimal("0.1545")
    assert billing.cost == pytest.approx(0.1545)
    assert "input_cost_per_token_cache_write" in billing.pricing_fields_used
    assert billing.usage_snapshot == {
        "prompt_tokens": 102_000,
        "completion_tokens": 1_000,
        "prompt_tokens_cached": 70_000,
        "prompt_tokens_cache_write": 30_000,
    }


def test_one_hour_writes_use_the_one_hour_price():
    billing = _charge(SONNET, {**TURN, "prompt_tokens_cache_write_1h": 30_000})

    # 30,000 x $6/M instead of 30,000 x $3.75/M.
    assert billing.exact_cost == Decimal("0.222")
    assert billing.cost == pytest.approx(0.222)
    assert "input_cost_per_token_cache_write_1h" in billing.pricing_fields_used
    assert billing.usage_snapshot["prompt_tokens_cache_write_1h"] == 30_000


def test_one_hour_writes_without_a_one_hour_price_use_the_write_price():
    info = {k: v for k, v in SONNET.items() if k != "input_cost_per_token_cache_write_1h"}
    billing = _charge(info, {**TURN, "prompt_tokens_cache_write_1h": 10_000})

    assert billing.exact_cost == Decimal("0.1545")
    assert billing.cost == pytest.approx(0.1545)


def test_models_without_a_write_price_keep_the_old_charge():
    info = {
        k: v
        for k, v in SONNET.items()
        if k not in {"input_cost_per_token_cache_write", "input_cost_per_token_cache_write_1h"}
    }
    with_writes = _charge(info, {**TURN, "prompt_tokens_cache_write_1h": 5_000})
    # Before cache-write prices, writes were ordinary prompt tokens.
    old_shape = _charge(info, {k: v for k, v in TURN.items() if k != "prompt_tokens_cache_write"})

    assert with_writes.exact_cost == old_shape.exact_cost == Decimal("0.132")
    assert with_writes.cost == pytest.approx(old_shape.cost)
    assert "input_cost_per_token_cache_write" not in with_writes.pricing_fields_used


def test_gateway_response_cache_hit_charges_no_provider_cache_write():
    replay = _charge(SONNET, TURN, cache_hit=True)
    no_writes = _charge(
        SONNET, {k: v for k, v in TURN.items() if k != "prompt_tokens_cache_write"}, cache_hit=True
    )

    assert replay.exact_cost == no_writes.exact_cost
    assert replay.cost == pytest.approx(no_writes.cost)
    assert "prompt_tokens_cache_write" not in replay.usage_snapshot


def test_legacy_float_cost_matches_the_exact_cost():
    pricing = pricing_from_model_info(SONNET)
    for usage in (TURN, {**TURN, "prompt_tokens_cache_write_1h": 12_000}):
        exact = _charge(SONNET, usage).exact_cost
        assert completion_cost(model="claude-sonnet", usage=usage, custom_pricing=pricing) == (
            pytest.approx(float(exact))
        )


def test_batch_multiplier_scales_the_legacy_write_price():
    from src.billing.pricing.cost import resolve_batch_pricing

    scaled = resolve_batch_pricing(
        sync_pricing=pricing_from_model_info(SONNET), model_info={"batch_price_multiplier": 0.5}
    )
    assert scaled is not None
    assert scaled.input_cost_per_token_cache_write == pytest.approx(0.000001875)
    assert scaled.input_cost_per_token_cache_write_1h == pytest.approx(0.000003)


def test_tier_write_price_overrides_the_deployment_price():
    resolution = replace(
        _resolution(SONNET),
        customer_model_info={**SONNET, "input_cost_per_token_cache_write": "0.000005"},
        tier_pricing_fields=("input_cost_per_token_cache_write",),
        tier_pricing_applied=True,
    )
    quote = resolve_exact_token_quote_pricing(
        resolution,
        model="claude-sonnet",
        prompt_tokens=100,
        completion_tokens=0,
        prompt_tokens_cache_write=100,
    )
    provider = resolve_exact_token_quote_pricing(
        resolution,
        model="claude-sonnet",
        prompt_tokens=100,
        completion_tokens=0,
        prompt_tokens_cache_write=100,
        pricing_view="provider",
    )

    assert quote.pricing is not None and provider.pricing is not None
    assert quote.pricing.input_cost_per_token_cache_write == Decimal("0.000005")
    assert "tier" in quote.pricing_sources_used
    assert provider.pricing.input_cost_per_token_cache_write == Decimal("0.00000375")


@pytest.mark.parametrize("value", ["-1", "abc"])
def test_invalid_write_price_leaves_the_call_unpriced(value):
    billing = _charge({**SONNET, "input_cost_per_token_cache_write": value}, TURN)

    assert billing.exact_cost is None
    assert billing.unpriced_reason == "invalid_configured_pricing"


@pytest.mark.parametrize(
    "counts",
    [
        {"prompt_tokens_cached": 6, "prompt_tokens_cache_write": 5},
        {"prompt_tokens_cache_write": 2, "prompt_tokens_cache_write_1h": 3},
        {"prompt_tokens_cache_write": -1},
    ],
)
def test_exact_receipt_rejects_impossible_write_counts(counts):
    with pytest.raises(ValueError, match="Invalid exact token receipt"):
        ExactTokenRates().cost(prompt_tokens=10, completion_tokens=0, **counts)


def test_prompt_split_clamps_counts_outside_the_strict_receipt():
    split = PromptTokenSplit.of(
        prompt_tokens=10, cached=6, cache_write=9, cache_write_1h=9, cache_hit=False
    )
    assert split == PromptTokenSplit(uncached=0, cached=6, cache_write_5m=0, cache_write_1h=4)


def test_hold_covers_a_write_price_above_the_input_price():
    resolution = _resolution(SONNET)
    hold = conservative_provider_allowance(
        pricing=resolution,
        model_info={"max_input_tokens": 1_000, "max_output_tokens": 10},
        call_type="completion",
        bounds=ProviderRequestBounds(max_output_tokens=10),
        max_attempts=1,
    )
    # Every prompt token at the 1-hour write price, plus the output tokens.
    assert hold == Decimal("1000") * Decimal("0.000006") + Decimal("10") * Decimal("0.000015")
    worst = _charge(
        SONNET,
        {
            "prompt_tokens": 1_000,
            "completion_tokens": 10,
            "prompt_tokens_cache_write": 1_000,
            "prompt_tokens_cache_write_1h": 1_000,
        },
    )
    assert worst.exact_cost <= hold


def test_frozen_accounting_snapshot_keeps_write_prices():
    resolution = replace(
        _resolution(SONNET),
        catalog_token_pricing=ModelPricing(input_cost_per_token_cache_write=0.1),
    )
    snapshot = accounting_pricing_snapshot(resolution)

    assert snapshot["customer.input_cost_per_token_cache_write"] == "0.00000375"
    assert snapshot["provider.input_cost_per_token_cache_write_1h"] == "0.000006"
    assert snapshot["catalog.input_cost_per_token_cache_write"] == "0.1"


def test_selector_classifier_rejects_paid_cache_writes():
    # The selector receipt, charge and hold have no write dimension.
    with pytest.raises(ValueError, match="unsupported provider billing dimensions"):
        selector_price_snapshot(SONNET)
    free_writes = {**SONNET, "input_cost_per_token_cache_write": 0}
    free_writes.pop("input_cost_per_token_cache_write_1h")

    assert selector_price_snapshot(free_writes).input_cost_per_token == Decimal("0.000003")


def test_native_usage_splits_one_hour_writes():
    usage = native_usage(
        {
            "input_tokens": 10,
            "output_tokens": 7,
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 1000,
            "cache_creation": {"ephemeral_5m_input_tokens": 60, "ephemeral_1h_input_tokens": 40},
        }
    )

    assert usage["prompt_tokens"] == 1110
    assert usage["prompt_tokens_cache_write"] == 100
    assert usage["prompt_tokens_cache_write_1h"] == 40


@pytest.mark.parametrize(
    "creation",
    [{"ephemeral_1h_input_tokens": 101}, {"ephemeral_1h_input_tokens": -1}, ["bad"]],
)
def test_native_usage_rejects_an_impossible_cache_creation_split(creation):
    from src.models.errors import ProxyError

    with pytest.raises(ProxyError):
        native_usage(
            {
                "input_tokens": 10,
                "output_tokens": 7,
                "cache_creation_input_tokens": 100,
                "cache_creation": creation,
            }
        )


def test_zero_cache_write_counts_stay_out_of_the_answer():
    plain = CompatibleChatUsage(prompt_tokens=3, completion_tokens=1, total_tokens=4)
    written = CompatibleChatUsage(
        prompt_tokens=3, completion_tokens=1, total_tokens=4, prompt_tokens_cache_write=2
    )

    assert "prompt_tokens_cache_write" not in plain.model_dump(mode="json")
    assert "prompt_tokens_cache_write_1h" not in written.model_dump(mode="json")
    assert written.model_dump(mode="json")["prompt_tokens_cache_write"] == 2


def _batch_charge(info: dict[str, object], usage: dict[str, int]):
    return resolve_token_billing_result(
        _resolution(info, mode="batch"), model="claude-sonnet", usage=usage, mode="batch"
    ).billing


@pytest.mark.parametrize("multiplier", [None, "0.5"])
def test_batch_write_price_follows_the_multiplier(multiplier):
    info = {"input_cost_per_token": "0.000003", "input_cost_per_token_cache_write_1h": "0.000006"}
    if multiplier is not None:
        info["batch_price_multiplier"] = multiplier
    usage = {
        "prompt_tokens": 10_000,
        "completion_tokens": 0,
        "prompt_tokens_cache_write": 10_000,
        "prompt_tokens_cache_write_1h": 10_000,
    }
    exact = _batch_charge(info, usage).exact_cost
    legacy = completion_cost(
        model="claude-sonnet",
        usage=usage,
        custom_pricing=pricing_from_model_info(info),
        pricing_tier="batch",
        model_info=info,
    )

    assert exact == (Decimal("0.03") if multiplier else Decimal("0.06"))
    assert legacy == pytest.approx(float(exact))


@pytest.mark.parametrize("multiplier", [None, "0.5"])
def test_batch_hold_covers_a_full_write_receipt(multiplier):
    info = {
        "input_cost_per_token": "0.000003",
        "output_cost_per_token": "0",
        "input_cost_per_token_cache_write_1h": "0.000006",
    }
    if multiplier is not None:
        info["batch_price_multiplier"] = multiplier
    hold = conservative_provider_allowance(
        pricing=_resolution(info, mode="batch"),
        model_info={"max_input_tokens": 10_000, "max_output_tokens": 1},
        call_type="completion",
        bounds=ProviderRequestBounds(max_output_tokens=1),
        max_attempts=1,
    )
    worst = _batch_charge(
        info,
        {
            "prompt_tokens": 10_000,
            "completion_tokens": 1,
            "prompt_tokens_cache_write": 10_000,
            "prompt_tokens_cache_write_1h": 10_000,
        },
    )

    assert worst.exact_cost == (Decimal("0.03") if multiplier else Decimal("0.06"))
    assert worst.exact_cost <= hold


def test_batch_receipt_keeps_write_counts():
    from src.batch.accounting_execution import _receipt_counts

    plain = {"prompt_tokens": 10, "completion_tokens": 1, "prompt_tokens_cached": 2}
    assert _receipt_counts(plain) == plain
    assert _receipt_counts(
        {**plain, "prompt_tokens_cache_write": 8, "prompt_tokens_cache_write_1h": 3}
    ) == {**plain, "prompt_tokens_cache_write": 8, "prompt_tokens_cache_write_1h": 3}


@pytest.mark.parametrize(
    "writes",
    [
        {"prompt_tokens_cache_write": 9},
        {"prompt_tokens_cache_write": 2, "prompt_tokens_cache_write_1h": 3},
        {"prompt_tokens_cache_write": "2"},
        {"prompt_tokens_cache_write": -1},
    ],
)
def test_batch_receipt_rejects_impossible_write_counts(writes):
    from src.batch.accounting_checkpoint import BatchAccountingUnavailable
    from src.batch.accounting_execution import _receipt_counts

    with pytest.raises(BatchAccountingUnavailable):
        _receipt_counts(
            {"prompt_tokens": 10, "completion_tokens": 1, "prompt_tokens_cached": 2, **writes}
        )
