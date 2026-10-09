"""Freeze all deployment, tier and catalog inputs before external execution."""

from copy import deepcopy
from dataclasses import replace

from src.billing.cost import get_model_pricing
from src.billing.tier_pricing import PricingResolution


def freeze_operation_pricing(pricing: PricingResolution) -> PricingResolution:
    if pricing.catalog_pricing_frozen:
        return pricing
    return replace(
        pricing,
        customer_model_info=deepcopy(pricing.customer_model_info),
        provider_model_info=deepcopy(pricing.provider_model_info),
        catalog_pricing_frozen=True,
        catalog_token_pricing=(
            get_model_pricing(pricing.provider_model) or get_model_pricing(pricing.callable_model)
        ),
    )
