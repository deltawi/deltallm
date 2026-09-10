from __future__ import annotations

import asyncio
from datetime import timedelta
import math
from typing import TYPE_CHECKING

from src.batch.selector_checkpoint import (
    CHECKPOINT_MAX_BYTES,
    BatchSelectorCheckpoint,
    BatchSelectorClaim,
    BatchSelectorUnavailable,
)
from src.batch.selector_identity import batch_selector_operation_id

if TYPE_CHECKING:
    from prisma import Prisma

WRITE_CHECKPOINT_SQL = """
    UPDATE deltallm_batch_item AS i SET selector_checkpoint=$6::jsonb
    FROM deltallm_batch_job AS j
    WHERE i.item_id=$1 AND i.batch_id=$2
      AND i.locked_by=$3 AND i.claim_epoch=$4
      AND i.status='in_progress' AND i.lease_expires_at>NOW()
      AND j.batch_id=i.batch_id AND j.created_by_api_key=$5
      AND j.status='in_progress' AND j.cancel_requested_at IS NULL
      AND (j.expires_at IS NULL OR j.expires_at>NOW())
      AND (i.selector_checkpoint IS NOT DISTINCT FROM $7::jsonb
           OR ($7::jsonb IS NOT NULL AND i.selector_checkpoint=$6::jsonb))
    RETURNING i.item_id
"""


class BatchSelectorRepository:
    """One bounded item checkpoint, using the existing worker claim and DB pool."""

    def __init__(self, db: Prisma) -> None:
        self._db = db

    async def write(
        self,
        claim: BatchSelectorClaim,
        *,
        expected: BatchSelectorCheckpoint | None,
        checkpoint: BatchSelectorCheckpoint,
        expires_at: float,
    ) -> None:
        if checkpoint.operation_id != batch_selector_operation_id(claim.batch_id, claim.item_id):
            raise BatchSelectorUnavailable()
        if expected is None:
            if checkpoint.decision is not None:
                raise BatchSelectorUnavailable()
        elif (
            expected.decision is not None
            or checkpoint.decision is None
            or expected.model_dump(exclude={"decision"})
            != checkpoint.model_dump(exclude={"decision"})
        ):
            raise BatchSelectorUnavailable()
        serialized = checkpoint.model_dump_json()
        remaining = min(0.25, expires_at - asyncio.get_running_loop().time())
        if (
            not math.isfinite(remaining)
            or remaining <= 0
            or len(serialized.encode()) > CHECKPOINT_MAX_BYTES
        ):
            raise BatchSelectorUnavailable()
        try:
            async with asyncio.timeout(remaining):
                async with self._db.tx(
                    max_wait=timedelta(seconds=remaining), timeout=timedelta(seconds=remaining)
                ) as tx:
                    await tx.query_raw(
                        "SELECT set_config('statement_timeout',$1,true), "
                        "set_config('lock_timeout',$1,true)",
                        f"{max(1, int(remaining * 1000))}ms",
                    )
                    rows = await tx.query_raw(
                        WRITE_CHECKPOINT_SQL,
                        claim.item_id,
                        claim.batch_id,
                        claim.worker_id,
                        claim.claim_epoch,
                        claim.api_key,
                        serialized,
                        expected.model_dump_json() if expected is not None else None,
                    )
                    if len(rows) != 1:
                        raise BatchSelectorUnavailable()
        except Exception:
            # Repository boundary: never leak SQL, persisted content or tenant identifiers.
            raise BatchSelectorUnavailable() from None
