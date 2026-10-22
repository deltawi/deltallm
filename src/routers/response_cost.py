"""The per-response cost header: what DeltaLLM recorded for this request, in USD."""

from __future__ import annotations

from fastapi import Request

from src.billing.money import canonical_money
from src.billing.pricing.cost import BillingResult

RESPONSE_COST_HEADER = "x-deltallm-response-cost"
_RESPONSE_COST_STATE = "response_cost_header"


def record_response_cost(request: Request, billing: BillingResult) -> None:
    """Keep the customer cost for the response; an unpriced request gets no header."""
    if billing.unpriced_reason is not None:
        return
    amount = canonical_money(billing.cost).normalize()
    setattr(request.state, _RESPONSE_COST_STATE, f"{amount:f}")


def response_cost_headers(request: Request) -> dict[str, str]:
    value = getattr(request.state, _RESPONSE_COST_STATE, None)
    return {RESPONSE_COST_HEADER: value} if isinstance(value, str) else {}
