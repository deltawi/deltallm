"""Reserve a cost ceiling from provider limits before each Realtime turn."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, localcontext

from src.billing.money import MONEY_QUANTUM, canonical_money
from src.billing.realtime_pricing import RealtimePrices
from src.billing.realtime_usage import RealtimeDurationUsage, RealtimeTokenUsage
from src.realtime.errors import RealtimeError


@dataclass(frozen=True, slots=True)
class RealtimeCostBounds:
    transcription: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    input_seconds: Decimal | None = None

    def __post_init__(self) -> None:
        if self.input_seconds is not None:
            if (
                not self.input_seconds.is_finite()
                or self.input_seconds <= 0
                or self.input_tokens is not None
                or self.output_tokens is not None
            ):
                raise ValueError("Invalid Realtime duration ceiling")
        elif any(
            type(value) is not int or not 0 < value < 2**31
            for value in (self.input_tokens, self.output_tokens)
        ):
            raise ValueError("Invalid Realtime token ceiling")

    def snapshot(self) -> dict[str, object]:
        return {
            "transcription": self.transcription,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "input_seconds": str(self.input_seconds) if self.input_seconds is not None else None,
        }

    def allowance(self, prices: RealtimePrices) -> Decimal:
        with localcontext() as context:
            context.prec = 80
            if self.input_seconds is not None:
                cost = self.input_seconds * prices.rates["seconds"]
            else:
                inputs = max(
                    prices.rates.get(name, Decimal(0))
                    for name in (
                        "input_text",
                        "input_audio",
                        "cached_input_text",
                        "cached_input_audio",
                    )
                )
                outputs = max(
                    prices.rates.get(name, Decimal(0)) for name in ("output_text", "output_audio")
                )
                cost = self.input_tokens * inputs + self.output_tokens * outputs
            return canonical_money(
                (cost + prices.per_operation).quantize(MONEY_QUANTUM, rounding=ROUND_CEILING)
            )

    def contains(self, usage: RealtimeTokenUsage | RealtimeDurationUsage) -> bool:
        if isinstance(usage, RealtimeDurationUsage):
            return self.input_seconds is not None and usage.seconds <= self.input_seconds
        if self.input_seconds is not None:
            return False
        inputs = (
            usage.input_text
            + usage.input_audio
            + usage.cached_input_text
            + usage.cached_input_audio
        )
        return (
            inputs <= self.input_tokens
            and usage.output_text + usage.output_audio <= self.output_tokens
        )


def realtime_cost_bounds(
    model_info: Mapping[str, object],
    *,
    transcription: bool,
    duration: bool,
    max_output_tokens: int,
    max_input_bytes: int,
) -> RealtimeCostBounds:
    if duration:
        # Duration admission requires confirmed mono PCM at 24 kHz / 16 bits.
        with localcontext() as context:
            context.prec = 80
            seconds = (Decimal(max_input_bytes) / Decimal(48_000)).quantize(
                MONEY_QUANTUM, rounding=ROUND_CEILING
            )
        return RealtimeCostBounds(transcription=True, input_seconds=seconds)
    inputs = model_info.get("max_input_tokens") or model_info.get("max_tokens")
    outputs = model_info.get("max_output_tokens") if transcription else max_output_tokens
    try:
        return RealtimeCostBounds(
            transcription=transcription, input_tokens=inputs, output_tokens=outputs
        )
    except ValueError:
        raise RealtimeError(
            "budget_profile_unsupported", "Realtime requires declared provider token ceilings"
        ) from None
