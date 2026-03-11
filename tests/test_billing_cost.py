from __future__ import annotations

from src.billing.audio_usage import normalize_transcription_usage
from src.billing.cost import ModelPricing, completion_cost, compute_billing_result, get_model_pricing
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


def test_audio_transcription_prefers_audio_token_pricing_when_usage_available() -> None:
    result = compute_billing_result(
        mode="audio_transcription",
        usage={"duration_seconds": 60, "prompt_tokens": 12, "input_audio_tokens": 100},
        model_info={
            "input_cost_per_second": 0.0001,
            "input_cost_per_token": 0.5,
            "input_cost_per_audio_token": 0.25,
        },
    )

    assert result.cost == 31.0
    assert result.billing_unit == "token"
    assert result.usage_snapshot["input_audio_tokens"] == 100


def test_audio_transcription_falls_back_to_second_pricing() -> None:
    result = compute_billing_result(
        mode="audio_transcription",
        usage={"duration_seconds": 90},
        model_info={"input_cost_per_second": 0.1, "output_cost_per_second": 0.05},
    )

    assert result.cost == 13.5
    assert result.billing_unit == "second"


def test_audio_transcription_applies_provider_billing_rules_to_duration() -> None:
    usage = normalize_transcription_usage(
        response_payload={"text": "hello", "duration": 2.0},
        file_size_bytes=16,
        provider="groq",
    )

    result = compute_billing_result(
        mode="audio_transcription",
        usage=usage,
        model_info={"input_cost_per_second": 0.111},
    )

    assert result.cost == 1.11
    assert result.billing_unit == "second"
    assert result.usage_snapshot["duration_seconds"] == 2.0
    assert result.usage_snapshot["billable_duration_seconds"] == 10.0


def test_audio_speech_uses_character_pricing() -> None:
    result = compute_billing_result(
        mode="audio_speech",
        usage={"input_characters": 1000},
        model_info={"input_cost_per_character": 0.002},
    )

    assert result.cost == 2.0
    assert result.billing_unit == "character"


def test_audio_speech_uses_token_pricing_when_usage_available() -> None:
    result = compute_billing_result(
        mode="audio_speech",
        usage={
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "input_audio_tokens": 3,
            "output_audio_tokens": 5,
        },
        model_info={
            "input_cost_per_token": 1.0,
            "output_cost_per_token": 2.0,
            "input_cost_per_audio_token": 3.0,
            "output_cost_per_audio_token": 4.0,
        },
    )

    assert result.cost == 47.0
    assert result.billing_unit == "token"


def test_audio_speech_marks_missing_usage_or_pricing_as_unpriced() -> None:
    result = compute_billing_result(
        mode="audio_speech",
        usage={"input_characters": 100},
        model_info={},
    )

    assert result.cost == 0.0
    assert result.unpriced_reason == "missing_tts_pricing_or_usage"
