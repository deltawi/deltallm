"""Select exact token rates once for legacy display and native accounting."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Literal

from src.billing.cost import ModelPricing
from src.billing.money import canonical_money

PricingMode = Literal["sync", "batch"]
PricingSource = Literal["tier", "deployment", "default"]
PricingView = Literal["customer", "provider"]
_ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class ExactTokenRates:
    input_cost_per_token: Decimal = _ZERO
    output_cost_per_token: Decimal = _ZERO
    input_cost_per_token_cache_hit: Decimal | None = None
    output_cost_per_token_cache_hit: Decimal | None = None
    cost_per_request: Decimal = _ZERO

    def __post_init__(self) -> None:
        rates = (
            self.input_cost_per_token,
            self.output_cost_per_token,
            self.input_cost_per_token_cache_hit,
            self.output_cost_per_token_cache_hit,
            self.cost_per_request,
        )
        if any(rate is not None and (not rate.is_finite() or rate < 0) for rate in rates):
            raise ValueError("Invalid exact token price")

    def cost(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        prompt_tokens_cached: int = 0,
        cache_hit: bool = False,
    ) -> Decimal:
        if (
            any(
                type(count) is not int or not 0 <= count < 2**31
                for count in (
                    prompt_tokens,
                    completion_tokens,
                    prompt_tokens_cached,
                )
            )
            or prompt_tokens_cached > prompt_tokens
        ):
            raise ValueError("Invalid exact token receipt")
        cached = prompt_tokens if cache_hit and prompt_tokens_cached == 0 else prompt_tokens_cached
        cache_rate = self.input_cost_per_token_cache_hit
        output_rate = (
            self.output_cost_per_token_cache_hit
            if cache_hit and self.output_cost_per_token_cache_hit is not None
            else self.output_cost_per_token
        )
        with localcontext() as context:
            context.prec = 80
            return canonical_money(
                (prompt_tokens - cached) * self.input_cost_per_token
                + cached * (self.input_cost_per_token if cache_rate is None else cache_rate)
                + completion_tokens * output_rate
                + self.cost_per_request
            )

    def legacy_pricing(self) -> ModelPricing:
        """Keep the existing float DTO at the legacy display boundary only."""
        return ModelPricing(
            input_cost_per_token=float(self.input_cost_per_token),
            output_cost_per_token=float(self.output_cost_per_token),
            input_cost_per_token_cache_hit=(
                None
                if self.input_cost_per_token_cache_hit is None
                else float(self.input_cost_per_token_cache_hit)
            ),
            output_cost_per_token_cache_hit=(
                None
                if self.output_cost_per_token_cache_hit is None
                else float(self.output_cost_per_token_cache_hit)
            ),
            cost_per_request=float(self.cost_per_request),
        )


@dataclass(frozen=True, slots=True)
class TokenQuoteContext:
    info: Mapping[str, object]
    mode: PricingMode
    view: PricingView
    catalog: ModelPricing | None
    provider_fields: tuple[str, ...]
    tier_fields: tuple[str, ...]
    tier_applied: bool

    def source(self, field: str) -> PricingSource:
        if self.view == "customer" and self.tier_applied and field in self.tier_fields:
            return "tier"
        return "deployment" if field in self.provider_fields else "default"


@dataclass(frozen=True, slots=True)
class ExactTokenQuote:
    pricing: ExactTokenRates | None
    pricing_fields_used: tuple[str, ...] = ()
    pricing_sources_used: tuple[PricingSource, ...] = ()
    missing_pricing_fields: tuple[str, ...] = ()
    unpriced_reason: str | None = None
    request_only: bool = False


@dataclass(frozen=True, slots=True)
class _SelectedRate:
    rate: Decimal | None
    field: str
    source: PricingSource | None


class _TokenQuotePolicy:
    def __init__(self, context: TokenQuoteContext) -> None:
        self.context = context
        self.fields: list[str] = []
        self.sources: set[PricingSource] = set()
        self.missing: list[str] = []
        self.rates: dict[str, Decimal] = {}
        self.invalid: set[str] = set()
        self.sync = any(
            self.configured(key) is not None
            for key in (
                "input_cost_per_token",
                "output_cost_per_token",
            )
        )
        self.batch = any(
            self.configured(key) is not None
            for key in (
                "batch_input_cost_per_token",
                "batch_output_cost_per_token",
            )
        )
        self.multiplier = self.configured("batch_price_multiplier")
        self.request_price = self.configured("cost_per_request")
        self.request_rate = self.request_price or _ZERO
        if self.request_price is not None:
            self.record(
                "cost_per_request",
                _SelectedRate(
                    self.request_price, "cost_per_request", context.source("cost_per_request")
                ),
            )
            self.request_rate = self.scaled(self.request_rate)

    def configured(self, field: str) -> Decimal | None:
        value = self.context.info.get(field)
        if value is None:
            return None
        try:
            rate = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            self.invalid.add(field)
            return None
        if not rate.is_finite() or rate < 0:
            self.invalid.add(field)
            return None
        return rate

    def scaled(self, value: Decimal) -> Decimal:
        if self.context.mode != "batch" or self.batch or self.multiplier is None:
            return value
        self.fields.append("batch_price_multiplier")
        self.sources.add(self.context.source("batch_price_multiplier"))
        with localcontext() as context:
            context.prec = 80
            return value * self.multiplier

    def regular(
        self, field: Literal["input_cost_per_token", "output_cost_per_token"]
    ) -> _SelectedRate:
        if self.context.mode == "batch" and self.batch:
            batch_field = (
                "batch_input_cost_per_token"
                if field == "input_cost_per_token"
                else ("batch_output_cost_per_token")
            )
            rate = self.configured(batch_field)
            if rate is not None:
                return _SelectedRate(rate, batch_field, self.context.source(batch_field))
        rate = self.configured(field)
        source = self.context.source(field) if rate is not None else None
        catalog = self.context.catalog
        if rate is None and not self.sync and catalog is not None:
            rate = Decimal(
                str(
                    catalog.input_cost_per_token
                    if field == "input_cost_per_token"
                    else catalog.output_cost_per_token
                )
            )
            source = "default"
        return _SelectedRate(None if rate is None else self.scaled(rate), field, source)

    def cached(
        self,
        cache_field: Literal["input_cost_per_token_cache_hit", "output_cost_per_token_cache_hit"],
        regular_field: Literal["input_cost_per_token", "output_cost_per_token"],
    ) -> _SelectedRate:
        rate = self.configured(cache_field)
        if rate is not None:
            return _SelectedRate(rate, cache_field, self.context.source(cache_field))
        catalog = self.context.catalog
        if not self.sync and catalog is not None:
            rate = (
                catalog.input_cost_per_token_cache_hit
                if cache_field == "input_cost_per_token_cache_hit"
                else catalog.output_cost_per_token_cache_hit
            )
            if rate is not None:
                return _SelectedRate(Decimal(str(rate)), cache_field, "default")
        return self.regular(regular_field)

    def record(self, target: str, value: _SelectedRate) -> None:
        if value.rate is None or value.source is None:
            self.missing.append(target)
            return
        self.rates[target] = value.rate
        self.fields.append(value.field)
        self.sources.add(value.source)

    def resolve(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        prompt_tokens_cached: int,
        cache_hit: bool,
    ) -> ExactTokenQuote:
        cache_fields = cache_hit and any(
            self.configured(key) is not None
            for key in (
                "input_cost_per_token_cache_hit",
                "output_cost_per_token_cache_hit",
            )
        )
        has_usage = prompt_tokens > 0 or completion_tokens > 0
        if self.invalid:
            return self.quote(None, unpriced_reason="invalid_configured_pricing")
        if not has_usage and self.request_price is None:
            return ExactTokenQuote(None, unpriced_reason="missing_usage_for_billing_mode")
        if not has_usage or (
            self.request_price is not None
            and not (self.sync or (self.context.mode == "batch" and self.batch) or cache_fields)
        ):
            return self.quote(
                ExactTokenRates(cost_per_request=self.request_rate), request_only=True
            )
        cached = min(prompt_tokens, prompt_tokens_cached)
        if cache_hit and prompt_tokens > 0 and cached == 0:
            cached = prompt_tokens
        if prompt_tokens > cached:
            self.record("input_cost_per_token", self.regular("input_cost_per_token"))
        if cached:
            self.record(
                "input_cost_per_token_cache_hit",
                self.cached("input_cost_per_token_cache_hit", "input_cost_per_token"),
            )
        if completion_tokens:
            selected = (
                self.cached("output_cost_per_token_cache_hit", "output_cost_per_token")
                if cache_hit
                else self.regular("output_cost_per_token")
            )
            field = (
                "output_cost_per_token_cache_hit"
                if selected.field == "output_cost_per_token_cache_hit"
                else "output_cost_per_token"
            )
            self.record(field, selected)
        if self.missing:
            return self.quote(None, unpriced_reason="no_configured_pricing")
        if self.invalid:
            return self.quote(None, unpriced_reason="invalid_configured_pricing")
        return self.quote(
            ExactTokenRates(
                input_cost_per_token=self.rates.get("input_cost_per_token", _ZERO),
                output_cost_per_token=self.rates.get("output_cost_per_token", _ZERO),
                input_cost_per_token_cache_hit=self.rates.get("input_cost_per_token_cache_hit"),
                output_cost_per_token_cache_hit=self.rates.get("output_cost_per_token_cache_hit"),
                cost_per_request=self.request_rate,
            )
        )

    def quote(
        self,
        pricing: ExactTokenRates | None,
        *,
        request_only: bool = False,
        unpriced_reason: str | None = None,
    ) -> ExactTokenQuote:
        return ExactTokenQuote(
            pricing,
            tuple(dict.fromkeys(self.fields)),
            tuple(sorted(self.sources)),
            tuple(self.missing),
            unpriced_reason,
            request_only,
        )


def resolve_exact_token_quote(
    context: TokenQuoteContext,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    prompt_tokens_cached: int = 0,
    cache_hit: bool = False,
) -> ExactTokenQuote:
    """The caller must supply frozen prices and normalized provider token counts."""
    return _TokenQuotePolicy(context).resolve(
        prompt_tokens=max(0, int(prompt_tokens)),
        completion_tokens=max(0, int(completion_tokens)),
        prompt_tokens_cached=max(0, int(prompt_tokens_cached)),
        cache_hit=cache_hit,
    )
