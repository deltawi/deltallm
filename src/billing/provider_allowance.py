"""Calculate the cost ceiling from validated request facts and frozen prices."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, DecimalException, ROUND_CEILING, localcontext

from src.billing.money import canonical_money
from src.billing.spend_operations import SpendPersistenceUnavailable
from src.billing.tier_pricing import PricingResolution


@dataclass(frozen=True, slots=True)
class ProviderRequestBounds:
    input_items: int = 1
    output_items: int = 1
    max_output_tokens: int | None = None
    input_characters: int | None = None

    def __post_init__(self) -> None:
        for value in (self.input_items, self.output_items):
            if _positive_int(value) is None:
                raise ValueError("request item counts must be positive bounded integers")
        if self.max_output_tokens is not None and (
            type(self.max_output_tokens) is not int or self.max_output_tokens <= 0
        ):
            raise ValueError("output token limit must be a positive integer")
        if self.input_characters is not None and (
            type(self.input_characters) is not int or not 0 <= self.input_characters < 2**31
        ):
            raise ValueError("input character count must be a bounded integer")


def conservative_provider_allowance(
    *,
    pricing: PricingResolution,
    model_info: Mapping[str, object],
    call_type: str,
    bounds: ProviderRequestBounds,
    max_attempts: int,
) -> Decimal:
    if _positive_int(max_attempts) is None:
        raise SpendPersistenceUnavailable()
    try:
        with localcontext() as context:
            context.prec = 80
            per_attempt = _attempt_allowance(pricing, model_info, call_type, bounds)
            return canonical_money(
                (per_attempt * max_attempts).quantize(Decimal("1e-18"), rounding=ROUND_CEILING)
            )
    except (DecimalException, ValueError):
        raise SpendPersistenceUnavailable() from None


def _attempt_allowance(
    pricing: PricingResolution,
    model_info: Mapping[str, object],
    call_type: str,
    bounds: ProviderRequestBounds,
) -> Decimal:
    info = pricing.customer_model_info
    request_rate = _decimal_rate(info.get("cost_per_request"))
    if call_type in {"completion", "embedding", "rerank"}:
        return _token_allowance(pricing, model_info, bounds, request_rate)
    if call_type == "image_generation":
        image_rate = max(
            _decimal_rate(info.get("input_cost_per_image")),
            _decimal_rate(info.get("output_cost_per_image")),
        )
        return bounds.output_items * image_rate + request_rate
    if call_type == "audio_speech":
        unsupported = (
            "input_cost_per_second",
            "output_cost_per_second",
            "input_cost_per_token",
            "output_cost_per_token",
            "input_cost_per_audio_token",
            "output_cost_per_audio_token",
        )
        if any(_decimal_rate(info.get(field)) > 0 for field in unsupported):
            raise SpendPersistenceUnavailable()
        if bounds.input_characters is None:
            raise SpendPersistenceUnavailable()
        character_rate = max(
            _decimal_rate(info.get("input_cost_per_character")),
            _decimal_rate(info.get("output_cost_per_character")),
        )
        return bounds.input_characters * character_rate + request_rate
    # An uploaded audio file has no provider-enforced duration or output bound.
    # Only a request price has an enforceable ceiling on this path.
    unsupported = (
        "input_cost_per_second",
        "output_cost_per_second",
        "input_cost_per_character",
        "output_cost_per_character",
        "input_cost_per_token",
        "output_cost_per_token",
        "input_cost_per_audio_token",
        "output_cost_per_audio_token",
    )
    if call_type != "audio_transcription" or any(
        _decimal_rate(info.get(field)) > 0 for field in unsupported
    ):
        raise SpendPersistenceUnavailable()
    return request_rate


def _token_allowance(
    pricing: PricingResolution,
    model_info: Mapping[str, object],
    bounds: ProviderRequestBounds,
    request_rate: Decimal,
) -> Decimal:
    tokens = pricing.customer_token_pricing or pricing.catalog_token_pricing
    if tokens is None:
        return request_rate
    input_rate = max(
        _decimal_rate(tokens.input_cost_per_token),
        _decimal_rate(tokens.input_cost_per_token_cache_hit),
    )
    output_rate = max(
        _decimal_rate(tokens.output_cost_per_token),
        _decimal_rate(tokens.output_cost_per_token_cache_hit),
    )
    input_ceiling = _positive_int(
        model_info.get("max_input_tokens") or model_info.get("max_tokens")
    ) or _positive_int(tokens.context_window)
    configured_output = _positive_int(
        model_info.get("max_output_tokens") or tokens.max_output_tokens
    )
    requested_output = bounds.max_output_tokens
    if requested_output is not None and _positive_int(requested_output) is None:
        raise SpendPersistenceUnavailable()
    # Local pricing metadata does not clamp the payload sent to the provider.
    # A larger validated output limit must not use a smaller declared default.
    output_ceiling = requested_output or configured_output or input_ceiling
    if input_ceiling is None or output_ceiling is None:
        raise SpendPersistenceUnavailable()
    # Each input can use a full context. Reserve a full context per output too;
    # this is safe when the provider shares prompt charges between choices.
    return (
        input_ceiling * input_rate * bounds.input_items * bounds.output_items
        + output_ceiling * output_rate * bounds.output_items
        + request_rate
    )


def _decimal_rate(value: object) -> Decimal:
    if value in (None, ""):
        return Decimal(0)
    try:
        rate = Decimal(str(value))
    except (DecimalException, TypeError, ValueError):
        raise SpendPersistenceUnavailable() from None
    if not rate.is_finite() or rate < 0:
        raise SpendPersistenceUnavailable()
    return rate


def _positive_int(value: object) -> int | None:
    if type(value) is not int or not 0 < value < 2**31:
        return None
    return value
