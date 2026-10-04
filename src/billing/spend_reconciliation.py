"""Reviewed operator evidence for an unknown ordinary provider operation."""

from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.billing.money import canonical_money


class SpendOperationResolution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    event_id: UUID
    organization_id: str | None = Field(default=None, max_length=512)
    actor_id: str = Field(min_length=1, max_length=512)
    evidence_reference: str = Field(min_length=1, max_length=512)
    outcome: Literal["usage_confirmed", "not_executed"]
    cost_exact: Decimal = Field(ge=0)
    provider_cost_exact: Decimal | None = Field(default=None, ge=0)
    usage: dict[str, int] = Field(default_factory=dict, max_length=24)

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        canonical_money(self.cost_exact)
        if self.provider_cost_exact is not None:
            canonical_money(self.provider_cost_exact)
        if any(len(key) > 64 or not 0 <= value < 2**31 for key, value in self.usage.items()):
            raise ValueError("usage must contain bounded nonnegative counters")
        if self.outcome == "not_executed" and (
            self.cost_exact or self.provider_cost_exact or any(self.usage.values())
        ):
            raise ValueError("non-execution evidence cannot have incurred usage or cost")
        return self
