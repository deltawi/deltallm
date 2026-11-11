"""Hold a funded batch operation open when its pre-call proof was not saved."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from src.batch.accounting_checkpoint import (
    BatchAccountingCheckpoint,
    BatchAccountingRejected,
    BatchAccountingUnavailable,
    BatchAccountingWrite,
)
from src.batch.accounting_execution import BatchAccountingExecution
from src.batch.accounting_native import NativeBatchBilling, deadline
from src.batch.retry import jittered_busy_wait

# The hold is the last write before the item is closed for good, and the
# busy database that refused the proof (a rollout, for example) usually
# frees up within seconds. So a hold refused for being busy is written
# again after each of these pauses before the worker gives up.
HOLD_RETRY_PAUSES_SECONDS = (0.5, 1.0, 2.0)


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
    provider failure. A first claim keeps its unsent attempt, settled with no
    usage. A proof that is already released needs no write. All other proofs
    are saved together, or none is.

    A first claim's proof can be saved although its reply was lost. When the
    database refuses the hold for that reason, the hold replaces that proof.
    """
    if not executions:
        return False
    hold_until = datetime.now(UTC) + timedelta(
        seconds=max(0, retry_delay_seconds) + max(0, claim_margin_seconds)
    )
    writes, unconfirmed = [], []
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
        released = _released(base, unsent=stored is None)
        writes.append(BatchAccountingWrite(execution.claim, stored, released))
        # The proof may have been saved although its reply was lost; then the
        # hold expects that proof instead. The database also accepts this
        # write as an exact repeat of a hold whose own reply was lost.
        unconfirmed.append(BatchAccountingWrite(execution.claim, base, released))
        held.append(released)
    # The proofs were sent in one write, so they were all saved or none was.
    choices = [writes] if unconfirmed == writes else [writes, unconfirmed]
    if writes and not await _write_hold(owner, choices):
        return False
    for execution, checkpoint in zip(executions, held, strict=True):
        execution.checkpoint = execution.stored_checkpoint = checkpoint
        execution.item.accounting_checkpoint = checkpoint.model_dump(mode="json")
        execution.proof_unsaved = False
        execution.released = True
    return True


def _released(base: BatchAccountingCheckpoint, *, unsent: bool) -> BatchAccountingCheckpoint:
    operation = base.operation
    if unsent:
        # A first claim's kept attempt never reached the provider, so it is
        # settled with no usage. Otherwise the retry's final charge would list
        # it as an unresolved attempt and keep the rest of the allowance
        # provisional, although nothing more can be owed for it.
        operation = operation.model_copy(update={"settled_attempts": len(operation.attempts)})
    return base.model_copy(
        update={"operation": operation, "released_attempts": len(operation.attempts)}
    )


async def _write_hold(
    owner: NativeBatchBilling, choices: Sequence[Sequence[BatchAccountingWrite]]
) -> bool:
    """Write the first of ``choices`` whose expected proofs are stored.

    A refused write is not repeated: the database answered, so only the next
    choice is tried, and once none is left (for example the claim was lost)
    the hold fails. A busy database is waited out instead.
    """
    remaining = iter(choices)
    writes = next(remaining)
    pauses = iter(HOLD_RETRY_PAUSES_SECONDS)
    while True:
        try:
            await owner.checkpoints.write_many(
                writes, expires_at=deadline(owner.checkpoint_write_seconds)
            )
            return True
        except BatchAccountingRejected:
            writes = next(remaining, None)
            if writes is None:
                return False
        except (BatchAccountingUnavailable, TimeoutError):
            pause = next(pauses, None)
            if pause is None:
                return False
            await _pause(pause)


async def _pause(seconds: float) -> None:
    await asyncio.sleep(jittered_busy_wait(seconds))
