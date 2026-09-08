from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, ROUND_CEILING, localcontext
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from src.billing.money import canonical_money
from src.billing.selector_charge import (
    AcceptedSelectorCharge,
    FrozenBillingContract,
    Identifier,
    Price,
    SelectorChargeAttribution,
    SelectorPriceSnapshot,
    SelectorTokenReceipt,
    TokenCount,
)
from src.models.errors import ServiceUnavailableError


class BillingOperationUnavailable(ServiceUnavailableError):
    error_type = "billing_operation_unavailable"
    message = "Billing operation is unavailable"

    def __init__(self) -> None:
        super().__init__(code="billing_operation_unavailable")


class ComponentState(StrEnum):
    RESERVED = "reserved"
    DISPATCHED = "dispatched"
    PENDING = "pending"
    ACCEPTED = "accepted"
    SETTLED = "settled"
    UNATTEMPTED = "unattempted"


class BoundedTokenQuote(FrozenBillingContract):
    """Server-qualified execution ceilings, never request token estimates.

    The execution owner must enforce all three ceilings. Unknown provider billing
    dimensions, uncapped retries/continuations or unbounded output cannot use this
    contract. For heterogeneous candidates quote their maximum applicable rate.
    """

    pricing: SelectorPriceSnapshot
    max_input_tokens: TokenCount
    max_output_tokens: TokenCount
    max_attempts: int = Field(ge=1, le=128)
    basis: Identifier

    @property
    def allowance(self) -> Decimal:
        price = self.pricing
        input_rate = max(
            price.input_cost_per_token, price.input_cost_per_token_cache_hit or Decimal(0)
        )
        with localcontext() as context:
            context.prec = 80
            per_attempt = (
                self.max_input_tokens * input_rate
                + self.max_output_tokens * price.output_cost_per_token
                + price.cost_per_request
            )
            amount = self.max_attempts * per_attempt.quantize(
                Decimal("1e-18"), rounding=ROUND_CEILING
            )
        return canonical_money(amount)


class ProviderEnforcedSelectorCeiling(FrozenBillingContract):
    """Trusted deployment capability, NOT a prompt estimate or a client setting.

    Bootstrap may construct this only from a qualified provider contract that caps
    all billable input (including server templates) at its context window, honors
    the selector's 64-token output cap, and has no other billable dimensions.
    Unknown/custom provider contracts cannot acquire this capability. Reserving the
    entire context window avoids assuming a characters-to-tokens conversion.
    """

    deployment_id: Identifier
    context_window_tokens: int = Field(ge=1, le=2**31 - 1)
    contract_version: Identifier
    billing_dimensions: Literal["input_output_cache_read_request"]


class OperationReservation(FrozenBillingContract):
    attribution: SelectorChargeAttribution = Field(repr=False)
    owner_token: UUID = Field(repr=False)
    selector: BoundedTokenQuote
    selector_ceiling: ProviderEnforcedSelectorCeiling
    answer: BoundedTokenQuote
    expires_at: AwareDatetime
    reference_answer_pricing: SelectorPriceSnapshot | None = None
    measurable_switch_penalty: Price | None = None
    reference_basis: Literal["uncached_answer_tokens:v1"] = "uncached_answer_tokens:v1"

    @model_validator(mode="after")
    def bounded_selector(self) -> OperationReservation:
        if self.selector.max_attempts != 1 or self.selector.max_output_tokens != 64:
            raise ValueError("selector reservation requires one bounded 64-token attempt")
        if (
            self.selector_ceiling.deployment_id != self.attribution.deployment_id
            or self.selector.max_input_tokens < self.selector_ceiling.context_window_tokens
        ):
            raise ValueError("selector quote must cover its qualified provider context ceiling")
        self.total_allowance
        return self

    @property
    def total_allowance(self) -> Decimal:
        with localcontext() as context:
            context.prec = 80
            return canonical_money(self.selector.allowance + self.answer.allowance)


class ReservedOperation(FrozenBillingContract):
    operation: OperationReservation = Field(repr=False)
    selector_state: ComponentState
    answer_state: ComponentState


class OperationReservationStore(Protocol):
    async def reserve(
        self, operation: OperationReservation, *, expires_at: float
    ) -> ReservedOperation: ...

    async def dispatch(
        self,
        operation: OperationReservation,
        *,
        component: Literal["selector", "answer"],
        expires_at: float,
    ) -> None: ...

    async def unattempted(
        self,
        operation: OperationReservation,
        *,
        component: Literal["selector", "answer"],
        expires_at: float,
    ) -> None: ...

    async def accept_selector(
        self, operation: OperationReservation, charge: AcceptedSelectorCharge, *, expires_at: float
    ) -> None: ...

    async def confirm_not_dispatched(
        self, operation: OperationReservation, *, expires_at: float
    ) -> None: ...


def selector_receipt(
    operation: OperationReservation, *, usage: SelectorTokenReceipt, started_at: datetime
) -> AcceptedSelectorCharge:
    return AcceptedSelectorCharge(
        attribution=operation.attribution,
        pricing=operation.selector.pricing,
        usage=usage,
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )
