"""Small fenced pages keep wide reporting payloads in PostgreSQL."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, model_validator

from src.billing.charges.selector_charge import FrozenBillingContract, Identifier

READ_MODEL_PROJECTION = "accounting-read-model-v2"
READ_MODEL_PAGE_BYTES = 1_048_576
READ_MODEL_HANDLE_BYTES = 16_384


class ReadModelClaim(FrozenBillingContract):
    generation: int = Field(ge=1, le=2**63 - 1)
    worker_id: Identifier
    lease_token: UUID = Field(repr=False)
    accounting_partition: int = Field(ge=0, le=63)
    after_sequence: int = Field(ge=0, le=2**63 - 1)
    sequences: tuple[int, ...] = Field(min_length=1, max_length=256)
    source_bytes: int = Field(ge=1, le=READ_MODEL_PAGE_BYTES)

    @model_validator(mode="after")
    def validate_page(self) -> ReadModelClaim:
        previous = self.after_sequence
        for sequence in self.sequences:
            if type(sequence) is not int or not previous < sequence <= 2**63 - 1:
                raise ValueError("read-model keys must be ordered after the checkpoint")
            previous = sequence
        if len(self.model_dump_json().encode()) > READ_MODEL_HANDLE_BYTES:
            raise ValueError("read-model handle exceeds its retained byte limit")
        return self


class ReadModelWorkerConfig(FrozenBillingContract):
    generation: int = Field(ge=1, le=2**63 - 1)
    worker_id: Identifier
    batch_size: int = Field(default=128, ge=1, le=256)
    lease_seconds: int = Field(default=30, ge=5, le=300)
    poll_seconds: float = Field(default=0.05, ge=0.01, le=5, allow_inf_nan=False)
    call_budget_seconds: float = Field(default=1, ge=0.04, le=2, allow_inf_nan=False)
    backoff_max_seconds: float = Field(default=5, ge=5, le=30, allow_inf_nan=False)
