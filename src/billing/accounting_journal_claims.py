"""Small fenced handles for bounded durable terminal processing."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator

from src.billing.selector_charge import FrozenBillingContract, Identifier


class JournalFailure(StrEnum):
    INVALID_PAYLOAD = "invalid_payload"
    PERSISTENCE = "persistence_unavailable"


class JournalClaim(FrozenBillingContract):
    protocol_generation: int = Field(ge=1, le=2**63 - 1)
    worker_id: Identifier
    lease_token: UUID = Field(repr=False)
    sequences: tuple[int, ...] = Field(max_length=256)

    @model_validator(mode="after")
    def validate_keys(self) -> JournalClaim:
        if len(set(self.sequences)) != len(self.sequences) or any(
            type(key) is not int or not 1 <= key <= 2**63 - 1 for key in self.sequences
        ):
            raise ValueError("terminal processing requires unique positive journal keys")
        return self
