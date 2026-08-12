from __future__ import annotations

from src.billing.audio_usage import normalize_speech_usage, normalize_transcription_usage
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


def test_batch_cost_uses_absolute_batch_pricing_without_sync_pricing() -> None:
    cost = completion_cost(
        model="custom-model",
        usage={"prompt_tokens": 5, "completion_tokens": 2},
        custom_pricing=None,
        pricing_tier="batch",
        model_info={
            "batch_input_cost_per_token": 1.0,
            "batch_output_cost_per_token": 1.5,
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


def test_audio_transcription_rejects_partially_priced_positive_token_usage() -> None:
    result = compute_billing_result(
        mode="audio_transcription",
        usage={"prompt_tokens": 12, "input_audio_tokens": 100},
        model_info={"input_cost_per_audio_token": 0.25},
    )

    assert result.cost == 0.0
    assert result.billing_unit is None
    assert result.pricing_fields_used == ("input_cost_per_audio_token",)
    assert result.missing_pricing_fields == ("input_cost_per_token",)
    assert result.unpriced_reason == "no_configured_pricing"


def test_audio_transcription_accepts_explicit_zero_for_each_positive_token_dimension() -> None:
    result = compute_billing_result(
        mode="audio_transcription",
        usage={"prompt_tokens": 12, "input_audio_tokens": 100},
        model_info={
            "input_cost_per_token": 0.0,
            "input_cost_per_audio_token": 0.25,
        },
    )

    assert result.cost == 25.0
    assert result.billing_unit == "token"
    assert result.missing_pricing_fields == ()
    assert result.unpriced_reason is None


def test_audio_transcription_falls_back_to_second_pricing() -> None:
    result = compute_billing_result(
        mode="audio_transcription",
        usage={"duration_seconds": 90},
        model_info={"input_cost_per_second": 0.1, "output_cost_per_second": 0.05},
    )

    assert result.cost == 13.5
    assert result.billing_unit == "second"


def test_compute_billing_result_adds_request_fee_to_token_modes() -> None:
    result = compute_billing_result(
        mode="rerank",
        usage={"prompt_tokens": 7, "completion_tokens": 0},
        model_info={"input_cost_per_token": 0.3, "cost_per_request": 0.4},
    )

    assert result.cost == 2.5
    assert result.billing_unit == "token"
    assert result.pricing_fields_used == (
        "input_cost_per_token",
        "cost_per_request",
    )


def test_compute_billing_result_supports_request_only_image_pricing() -> None:
    result = compute_billing_result(
        mode="image_generation",
        usage={"images": 2},
        model_info={"cost_per_request": 0.75},
    )

    assert result.cost == 0.75
    assert result.billing_unit == "request"
    assert result.pricing_fields_used == ("cost_per_request",)


def test_compute_billing_result_marks_explicit_zero_request_only_image_pricing() -> None:
    result = compute_billing_result(
        mode="image_generation",
        usage={"images": 2},
        model_info={"cost_per_request": 0.0},
    )

    assert result.cost == 0.0
    assert result.billing_unit == "request"
    assert result.pricing_fields_used == ("cost_per_request",)


def test_compute_billing_result_prefers_output_price_for_generated_images() -> None:
    result = compute_billing_result(
        mode="image_generation",
        usage={"images": 2},
        model_info={"input_cost_per_image": 0.25, "output_cost_per_image": 0.75},
    )

    assert result.cost == 1.5
    assert result.pricing_fields_used == ("output_cost_per_image",)
    assert result.usage_snapshot == {"images": 2, "output_images": 2, "input_images": 0}


def test_compute_billing_result_charges_explicit_input_and_output_images_separately() -> None:
    result = compute_billing_result(
        mode="image_generation",
        usage={"input_images": 3, "output_images": 2},
        model_info={"input_cost_per_image": 0.25, "output_cost_per_image": 0.75},
    )

    assert result.cost == 2.25
    assert result.pricing_fields_used == (
        "output_cost_per_image",
        "input_cost_per_image",
    )


def test_compute_billing_result_rejects_missing_input_image_price() -> None:
    result = compute_billing_result(
        mode="image_generation",
        usage={"input_images": 3, "output_images": 2},
        model_info={"output_cost_per_image": 0.75},
    )

    assert result.cost == 0.0
    assert result.unpriced_reason == "no_configured_pricing"
    assert result.missing_pricing_fields == ("input_cost_per_image",)
    assert result.usage_snapshot == {
        "images": 2,
        "output_images": 2,
        "input_images": 3,
    }


def test_compute_billing_result_adds_request_fee_to_audio_speech() -> None:
    result = compute_billing_result(
        mode="audio_speech",
        usage={"input_characters": 1000},
        model_info={"input_cost_per_character": 0.002, "cost_per_request": 0.5},
    )

    assert result.cost == 2.5
    assert result.billing_unit == "character"
    assert result.pricing_fields_used == (
        "input_cost_per_character",
        "cost_per_request",
    )


def test_compute_billing_result_supports_request_only_audio_transcription_pricing() -> None:
    result = compute_billing_result(
        mode="audio_transcription",
        usage={"duration_seconds": 30},
        model_info={"cost_per_request": 0.6},
    )

    assert result.cost == 0.6
    assert result.billing_unit == "request"
    assert result.pricing_fields_used == ("cost_per_request",)


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


def test_normalize_speech_usage_deduplicates_generic_audio_output_tokens() -> None:
    usage = normalize_speech_usage(
        request_text="hello",
        response_payload={
            "usage": {
                "input_tokens": 10,
                "output_tokens": 4,
                "output_audio_tokens": 4,
            }
        },
    )

    assert usage["prompt_tokens"] == 10
    assert usage["completion_tokens"] == 0
    assert usage["output_audio_tokens"] == 4


def test_audio_speech_rejects_partially_priced_positive_token_usage() -> None:
    result = compute_billing_result(
        mode="audio_speech",
        usage={"prompt_tokens": 10, "output_audio_tokens": 4},
        model_info={"output_cost_per_audio_token": 0.5},
    )

    assert result.cost == 0.0
    assert result.pricing_fields_used == ("output_cost_per_audio_token",)
    assert result.missing_pricing_fields == ("input_cost_per_token",)
    assert result.unpriced_reason == "no_configured_pricing"


def test_audio_speech_rejects_partially_priced_positive_character_usage() -> None:
    result = compute_billing_result(
        mode="audio_speech",
        usage={"input_characters": 100, "output_characters": 20},
        model_info={"input_cost_per_character": 0.01},
    )

    assert result.cost == 0.0
    assert result.pricing_fields_used == ("input_cost_per_character",)
    assert result.missing_pricing_fields == ("output_cost_per_character",)
    assert result.unpriced_reason == "no_configured_pricing"


def test_audio_speech_marks_missing_usage_or_pricing_as_unpriced() -> None:
    result = compute_billing_result(
        mode="audio_speech",
        usage={"input_characters": 100},
        model_info={},
    )

    assert result.cost == 0.0
    assert result.unpriced_reason == "missing_tts_pricing_or_usage"


def test_audio_speech_preserves_zero_price_for_matching_usage() -> None:
    result = compute_billing_result(
        mode="audio_speech",
        usage={"input_characters": 100},
        model_info={"input_cost_per_character": 0.0},
    )

    assert result.cost == 0.0
    assert result.billing_unit == "character"
    assert result.unpriced_reason is None


def test_audio_speech_does_not_apply_unrelated_zero_price() -> None:
    result = compute_billing_result(
        mode="audio_speech",
        usage={"input_characters": 100},
        model_info={"output_cost_per_audio_token": 0.0},
    )

    assert result.cost == 0.0
    assert result.unpriced_reason == "missing_tts_pricing_or_usage"


def test_audio_transcription_preserves_explicit_zero_request_price() -> None:
    result = compute_billing_result(
        mode="audio_transcription",
        usage={},
        model_info={"cost_per_request": 0.0},
    )

    assert result.cost == 0.0
    assert result.billing_unit == "request"
    assert result.unpriced_reason is None
