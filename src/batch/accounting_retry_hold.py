"""Hold a funded batch operation open when its pre-call proof was not saved."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from src.batch.accounting_checkpoint import BatchAccountingUnavailable, BatchAccountingWrite
from src.batch.accounting_execution import BatchAccountingExecution
from src.batch.accounting_native import NativeBatchBilling, deadline


async def hold_unsaved_for_retry(
    owner: NativeBatchBilling,
    executions: Sequence[BatchAccountingExecution],
    *,
    retry_delay_seconds: int,
    claim_margin_seconds: int,
) -> bool:
    """Keep funded operations open when their pre-call proof was not saved.

    The proof write failed, usually on a busy database, so the provider
    was never called for the new attempt. Closing the operation would end
    the item for good, so instead the unsent attempt is dropped and the
    operation is stored with every recorded attempt released: the next
    claim adds one attempt under the same funding, as after a retryable
    provider failure. A proof that is already released needs no write. All
    other proofs are saved together, or none is.
    """
    if not executions:
        return False
    hold_until = datetime.now(UTC) + timedelta(
        seconds=max(0, retry_delay_seconds) + max(0, claim_margin_seconds)
    )
    writes = []
    held = []
    for execution in executions:
        stored, checkpoint = execution.stored_checkpoint, execution.checkpoint
        if (
            not execution.proof_unsaved
            or checkpoint is None
            or checkpoint.terminal is not None
            or checkpoint == stored
            or execution.completion_saved
            or execution.released
        ):
            return False
        # Only a first claim has nothing stored: its unsent attempt is the
        # one the funding was admitted with, and it cannot be dropped.
        base = checkpoint if stored is None else stored
        if (
            base.terminal is not None
            or len(base.operation.attempts) >= owner.max_provider_attempts
            or base.operation.reservation.expires_at <= hold_until
        ):
            return False
        if base.retry_ready:
            held.append(base)
            continue
        released = base.model_copy(update={"released_attempts": len(base.operation.attempts)})
        writes.append(BatchAccountingWrite(execution.claim, stored, released))
        held.append(released)
    if writes:
        try:
            await owner.checkpoints.write_many(
                writes, expires_at=deadline(owner.checkpoint_write_seconds)
            )
        except (BatchAccountingUnavailable, TimeoutError):
            return False
    for execution, checkpoint in zip(executions, held, strict=True):
        execution.checkpoint = execution.stored_checkpoint = checkpoint
        execution.item.accounting_checkpoint = checkpoint.model_dump(mode="json")
        execution.proof_unsaved = False
        execution.released = True
    return True
