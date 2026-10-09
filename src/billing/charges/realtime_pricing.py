from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from types import MappingProxyType

from src.billing.money import canonical_money
from src.billing.charges.realtime_usage import (
    RealtimeDurationUsage,
    RealtimeTokenUsage,
    price_realtime_usage,
)

RATE_FIELDS = {
    "input_text": "input_cost_per_token",
    "output_text": "output_cost_per_token",
    "cached_input_text": "input_cost_per_token_cache_hit",
    "input_audio": "input_cost_per_audio_token",
    "output_audio": "output_cost_per_audio_token",
    "cached_input_audio": "input_cost_per_audio_token_cache_hit",
    "seconds": "input_cost_per_second",
}


@dataclass(frozen=True, slots=True)
class RealtimePrices:
    rates: Mapping[str, Decimal]
    per_operation: Decimal

    @classmethod
    def from_model_info(cls, info: Mapping[str, object]) -> RealtimePrices:
        rates = {
            unit: _price(info[field])
            for unit, field in RATE_FIELDS.items()
            if info.get(field) is not None
        }
        # Missing cache discounts use the configured full input rate, never zero.
        for cached, regular in (
            ("cached_input_text", "input_text"),
            ("cached_input_audio", "input_audio"),
        ):
            if cached not in rates and regular in rates:
                rates[cached] = rates[regular]
        fee = info.get("cost_per_request")
        return cls(MappingProxyType(rates), _price(0 if fee is None else fee))

    def require_profile(self, *, transcription: bool, duration: bool = False) -> None:
        required = {"seconds"} if duration else {"input_text", "input_audio", "output_text"}
        if not transcription:
            required.add("output_audio")
        if not required <= self.rates.keys():
            raise ValueError("Realtime deployment is missing required prices")

    def cost(self, usage: RealtimeTokenUsage | RealtimeDurationUsage) -> Decimal:
        with localcontext() as context:
            context.prec = 80
            return canonical_money(
                price_realtime_usage(usage, prices=self.rates) + self.per_operation
            )

    def snapshot(self) -> dict[str, object]:
        return {
            "currency": "USD",
            "rounding": "ROUND_HALF_EVEN",
            "rates": {key: str(value) for key, value in self.rates.items()},
            "per_operation": str(self.per_operation),
        }


def _price(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Invalid Realtime price")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid Realtime price") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid Realtime price")
    return canonical_money(result)
