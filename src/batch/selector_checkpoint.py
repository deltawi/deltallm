from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field, model_validator
from src.batch.public_errors import BatchPublicError, BatchPublicErrorCode

from src.router.selection.contracts import FrozenContract, SelectorDecision, SelectorPolicyIdentity

CHECKPOINT_MAX_BYTES = 4096


class BatchSelectorUnavailable(BatchPublicError):
    def __init__(self) -> None:
        super().__init__(BatchPublicErrorCode.SELECTOR_CHECKPOINT_UNAVAILABLE)


class BatchSelectorCheckpoint(FrozenContract):
    version: Literal[1] = 1
    operation_id: UUID
    input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_group: str = Field(min_length=1, max_length=256)
    policy_identity: SelectorPolicyIdentity
    decision: SelectorDecision | None = None

    @model_validator(mode="after")
    def validate_decision_identity(self) -> BatchSelectorCheckpoint:
        if self.decision is not None and self.decision.policy_identity != self.policy_identity:
            raise ValueError("checkpoint decision does not match its policy")
        return self


@dataclass(frozen=True, slots=True)
class BatchSelectorClaim:
    batch_id: str
    item_id: str
    api_key: str
    worker_id: str
    claim_epoch: int


class BatchSelectorCheckpoints(Protocol):
    async def write(
        self,
        claim: BatchSelectorClaim,
        *,
        expected: BatchSelectorCheckpoint | None,
        checkpoint: BatchSelectorCheckpoint,
        expires_at: float,
    ) -> None:
        """Compare-and-set on the authoritative primary, fenced by the active item claim."""
        ...
