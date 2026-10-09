"""Keep the same frozen rate fields for all accounting features."""

from __future__ import annotations

from src.billing.pricing.tier_pricing import PricingResolution


def accounting_pricing_snapshot(pricing: PricingResolution) -> dict[str, str | int | bool | None]:
    # Only billing dimensions and version identifiers; never arbitrary model_info.
    result: dict[str, str | int | bool | None] = {
        "source": pricing.source,
        "currency": "USD",
        "rounding": "ROUND_HALF_EVEN:1e-18",
        "tier_version_id": pricing.tier_version_id,
        "tier_assignment_id": pricing.tier_assignment_id,
    }
    for view, fields in (
        ("customer", pricing.customer_model_info),
        ("provider", pricing.provider_model_info),
    ):
        for field in (
            "input_cost_per_token",
            "output_cost_per_token",
            "input_cost_per_token_cache_hit",
            "output_cost_per_token_cache_hit",
            "batch_input_cost_per_token",
            "batch_output_cost_per_token",
            "batch_price_multiplier",
            "input_cost_per_character",
            "output_cost_per_character",
            "input_cost_per_second",
            "output_cost_per_second",
            "input_cost_per_image",
            "output_cost_per_image",
            "input_cost_per_audio_token",
            "output_cost_per_audio_token",
            "cost_per_request",
        ):
            value = fields.get(field)
            if value is not None:
                result[f"{view}.{field}"] = str(value)
    catalog = pricing.catalog_token_pricing
    if catalog is not None:
        for field in (
            "input_cost_per_token",
            "output_cost_per_token",
            "input_cost_per_token_cache_hit",
            "output_cost_per_token_cache_hit",
            "cost_per_request",
        ):
            value = getattr(catalog, field)
            if value is not None:
                result[f"catalog.{field}"] = str(value)
    return result
