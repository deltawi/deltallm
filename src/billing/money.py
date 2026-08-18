from __future__ import annotations

from decimal import Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
from typing import Any

MONEY_QUANTUM = Decimal("0.000000000000000001")
MONEY_MAX_ABS = Decimal("100000000000000000000")


def canonical_money(value: Any) -> Decimal:
    """Return one NUMERIC(38,18)-compatible monetary representation."""

    try:
        amount = Decimal(str(value if value is not None else 0))
        if not amount.is_finite():
            raise ValueError("money amount must be finite")
        # Decimal's process-default precision is commonly 28, below the 38
        # digits accepted by the database contract. Keep canonicalization
        # independent of ambient context and leave room for rounding carry.
        with localcontext() as context:
            context.prec = 80
            amount = amount.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)
    except (DecimalException, TypeError, ValueError) as exc:
        raise ValueError("money amount is outside NUMERIC(38,18) range") from exc
    if amount.copy_abs() >= MONEY_MAX_ABS:
        raise ValueError("money amount is outside NUMERIC(38,18) range")
    return amount


def money_string(value: Any) -> str:
    return format(canonical_money(value), "f")
