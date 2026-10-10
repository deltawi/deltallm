from __future__ import annotations

from datetime import UTC
from decimal import Decimal, localcontext
from typing import Annotated, Literal
from uuid import UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from src.billing.money import MONEY_MAX_ABS, canonical_money, money_string

SELECTOR_CHARGE_PURPOSE = "selector:v1"
SELECTOR_CALL_TYPE = "model_router_selector"
Identifier = Annotated[str, Field(min_length=1, max_length=256)]
Price = Annotated[
    Decimal, Field(ge=0, lt=MONEY_MAX_ABS, allow_inf_nan=False, max_digits=38, decimal_places=18)
]
TokenCount = Annotated[int, Field(ge=0, le=2**31 - 1)]


class FrozenBillingContract(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid", hide_input_in_errors=True)


class SelectorTokenReceipt(FrozenBillingContract):
    """A reported receipt, never an estimate or an unknown/unattempted placeholder."""

    kind: Literal["reported"] = "reported"
    prompt_tokens: TokenCount
    completion_tokens: TokenCount
    total_tokens: TokenCount
    cached_input_tokens: TokenCount | None = None

    @model_validator(mode="after")
    def validate_counts(self) -> SelectorTokenReceipt:
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("selector receipt token totals are inconsistent")
        if self.cached_input_tokens is not None and self.cached_input_tokens > self.prompt_tokens:
            raise ValueError("selector receipt cached input exceeds input tokens")
        return self


class SelectorPriceSnapshot(FrozenBillingContract):
    """Explicit token/request pricing only; unsupported price dimensions fail closed upstream."""

    source: Identifier
    version: Identifier
    currency: Literal["USD"] = "USD"
    rounding: Literal["ROUND_HALF_EVEN"] = "ROUND_HALF_EVEN"
    input_cost_per_token: Price
    output_cost_per_token: Price
    input_cost_per_token_cache_hit: Price | None = None
    cost_per_request: Price

    def cost(self, receipt: SelectorTokenReceipt) -> Decimal:
        cached_rate = self.input_cost_per_token_cache_hit
        if cached_rate is not None and cached_rate != self.input_cost_per_token:
            if receipt.cached_input_tokens is None:
                raise ValueError("selector receipt is missing required cached-input usage")
        cached = receipt.cached_input_tokens or 0
        with localcontext() as context:
            context.prec = 80
            amount = (
                (receipt.prompt_tokens - cached) * self.input_cost_per_token
                + cached * (cached_rate if cached_rate is not None else self.input_cost_per_token)
                + receipt.completion_tokens * self.output_cost_per_token
                + self.cost_per_request
            )
        return canonical_money(amount)


class SelectorChargeAttribution(FrozenBillingContract):
    """Resolved server-side attribution; accepting this type does not authorize its IDs."""

    operation_id: UUID = Field(repr=False)
    api_key: Identifier = Field(repr=False)
    user_id: Identifier | None = Field(default=None, repr=False)
    team_id: Identifier | None = Field(default=None, repr=False)
    organization_id: Identifier | None = Field(default=None, repr=False)
    owner_account_id: Identifier | None = Field(default=None, repr=False)
    end_user_id: Identifier | None = Field(default=None, repr=False)
    model_group: Identifier
    deployment_id: Identifier
    deployment_model: Identifier
    provider: Identifier

    @property
    def component_event_id(self) -> str:
        return str(uuid5(self.operation_id, SELECTOR_CHARGE_PURPOSE))


class AcceptedSelectorCharge(FrozenBillingContract):
    """Frozen receipt-to-spend mapping; durability remains owned by spend ingestion."""

    attribution: SelectorChargeAttribution = Field(repr=False)
    pricing: SelectorPriceSnapshot
    usage: SelectorTokenReceipt
    started_at: AwareDatetime
    finished_at: AwareDatetime

    @model_validator(mode="after")
    def validate_charge(self) -> AcceptedSelectorCharge:
        if self.finished_at < self.started_at:
            raise ValueError("selector receipt finishes before it starts")
        if (self.finished_at - self.started_at).total_seconds() * 1000 > 2**31 - 1:
            raise ValueError("selector receipt duration exceeds spend storage bounds")
        self.pricing.cost(self.usage)
        return self

    @property
    def provider_cost(self) -> Decimal:
        return self.pricing.cost(self.usage)

    @property
    def customer_charge(self) -> Decimal:
        # Product decision: pass-through provider cost, no selector markup.
        return self.provider_cost

    def spend_payload(self) -> dict[str, object]:
        owner = self.attribution
        return {
            "request_id": str(owner.operation_id),
            "api_key": owner.api_key,
            "user_id": owner.user_id,
            "team_id": owner.team_id,
            "organization_id": owner.organization_id,
            "owner_account_id": owner.owner_account_id,
            "owner_snapshot_complete": True,
            "end_user_id": owner.end_user_id,
            "model": owner.model_group,
            "call_type": SELECTOR_CALL_TYPE,
            "usage": {
                "prompt_tokens": self.usage.prompt_tokens,
                "completion_tokens": self.usage.completion_tokens,
                "total_tokens": self.usage.total_tokens,
                "prompt_tokens_cached": self.usage.cached_input_tokens,
            },
            "cost": money_string(self.customer_charge),
            "cost_exact": money_string(self.customer_charge),
            "provider_cost_exact": money_string(self.provider_cost),
            "cache_hit": False,
            "start_time": self.started_at.astimezone(UTC),
            "end_time": self.finished_at.astimezone(UTC),
            "metadata": {
                "component_purpose": SELECTOR_CHARGE_PURPOSE,
                "parent_event_id": str(owner.operation_id),
                "deployment_id": owner.deployment_id,
                "deployment_model": owner.deployment_model,
                "provider": owner.provider,
                "provider_cost": money_string(self.provider_cost),
                "selector_pricing": self.pricing.model_dump(mode="json"),
                "billing": {"billing_unit": "token", "usage_snapshot": self.usage.model_dump()},
            },
        }
