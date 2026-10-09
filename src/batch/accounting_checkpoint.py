"""Store one bounded provider replay fence under the existing batch claim."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID, uuid5

from pydantic import Field, model_validator

from src.batch.public_errors import BatchPublicError, BatchPublicErrorCode
from src.batch.selector_checkpoint import BatchSelectorClaim
from src.batch.selector_identity import batch_selector_operation_id
from src.billing.accounting_local_leases import LocalAccountingHandle
from src.billing.accounting_protocol import (
    AccountingFinalization,
    AccountingOperationHandle,
    AccountingOutcome,
)
from src.billing.selector_charge import FrozenBillingContract

CHECKPOINT_MAX_BYTES = 65_536
PROOF_MAX_BYTES = 24_576
CHECKPOINT_BATCH_MAX_BYTES = 1_048_576
CHECKPOINT_BATCH_MAX_ITEMS = 128


class BatchAccountingUnavailable(BatchPublicError):
    def __init__(self) -> None:
        super().__init__(BatchPublicErrorCode.ACCOUNTING_CHECKPOINT_UNAVAILABLE)


class BatchAccountingCheckpoint(FrozenBillingContract):
    version: Literal[1] = 1
    operation_id: UUID
    claim_epoch: int = Field(ge=1, le=2**63 - 1)
    operation: LocalAccountingHandle | AccountingOperationHandle = Field(repr=False)
    terminal: AccountingFinalization | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def validate_proofs(self) -> BatchAccountingCheckpoint:
        reservation = self.operation.reservation
        if (
            self.operation_id != reservation.operation_id
            or self.operation.dispatch_token != reservation.owner_token
            or len(self.operation.model_dump_json().encode()) > PROOF_MAX_BYTES
        ):
            raise ValueError("Invalid batch accounting proof")
        terminal = self.terminal
        if terminal is not None and (
            terminal.protocol_generation != reservation.protocol_generation
            or terminal.operation_id != reservation.operation_id
            or terminal.owner_token != reservation.owner_token
            or terminal.request_fingerprint != reservation.request_fingerprint
            or terminal.event_id != uuid5(reservation.operation_id, "provider-finalization:v2")
            or len(terminal.model_dump_json().encode()) > PROOF_MAX_BYTES
        ):
            raise ValueError("Invalid batch terminal proof")
        if len(self.model_dump_json().encode()) > CHECKPOINT_MAX_BYTES:
            raise ValueError("Batch accounting checkpoint exceeds its bound")
        return self


@dataclass(frozen=True, slots=True)
class BatchAccountingWrite:
    claim: BatchSelectorClaim
    expected: BatchAccountingCheckpoint | None
    checkpoint: BatchAccountingCheckpoint

    def validate(self) -> None:
        claim, expected, checkpoint = self.claim, self.expected, self.checkpoint
        try:
            BatchAccountingCheckpoint.model_validate_json(checkpoint.model_dump_json())
        except ValueError:
            raise BatchAccountingUnavailable() from None
        if (
            checkpoint.operation_id != batch_selector_operation_id(claim.batch_id, claim.item_id)
            or checkpoint.claim_epoch != claim.claim_epoch
            or checkpoint.operation.reservation.attribution.api_key != claim.api_key
        ):
            raise BatchAccountingUnavailable()
        if expected is None:
            if checkpoint.terminal is not None and (
                checkpoint.terminal.outcome is not AccountingOutcome.NOT_DISPATCHED
            ):
                raise BatchAccountingUnavailable()
            return
        if (
            expected.operation_id != checkpoint.operation_id
            or expected.claim_epoch > claim.claim_epoch
        ):
            raise BatchAccountingUnavailable()
        if expected.terminal is not None:
            if expected.model_copy(update={"claim_epoch": claim.claim_epoch}) != checkpoint:
                raise BatchAccountingUnavailable()
            return
        old_operation, new_operation = expected.operation, checkpoint.operation
        if old_operation.model_dump(exclude={"attempts"}) != new_operation.model_dump(
            exclude={"attempts"}
        ):
            raise BatchAccountingUnavailable()
        if (
            new_operation.attempts[: len(old_operation.attempts)] != old_operation.attempts
            or len(new_operation.attempts) > len(old_operation.attempts) + 1
            or (
                expected.claim_epoch != claim.claim_epoch
                and (new_operation != old_operation or checkpoint.terminal is None)
            )
        ):
            raise BatchAccountingUnavailable()


class BatchAccountingCheckpoints(Protocol):
    async def write_many(
        self,
        writes: Sequence[BatchAccountingWrite],
        *,
        expires_at: float,
    ) -> None:
        """Compare and save proofs under live primary claims in one SQL batch."""
        ...
