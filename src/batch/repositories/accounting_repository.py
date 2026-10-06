"""Use the existing primary batch pool for bounded execution recovery proofs."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import timedelta
import json
import math
from typing import TYPE_CHECKING

from src.batch.accounting_checkpoint import (
    CHECKPOINT_BATCH_MAX_BYTES,
    CHECKPOINT_BATCH_MAX_ITEMS,
    BatchAccountingUnavailable,
    BatchAccountingWrite,
)
from src.batch.worker_constants import COMPLETION_OUTBOX_MAX_ATTEMPTS
from src.batch.repositories.completion_outbox_fences import SENT_SQL, execute_completion_transition

if TYPE_CHECKING:
    from prisma import Prisma

WRITE_CHECKPOINTS_SQL = """
WITH requests AS MATERIALIZED (
    SELECT v->>'item_id' AS item_id, v->>'batch_id' AS batch_id,
           v->>'worker_id' AS worker_id, (v->>'claim_epoch')::bigint AS claim_epoch,
           v->>'api_key' AS api_key, v->'checkpoint' AS checkpoint,
           NULLIF(v->'expected','null'::jsonb) AS expected
    FROM jsonb_array_elements($1::jsonb) AS v
), owned AS MATERIALIZED (
    SELECT i.item_id
    FROM requests AS r
    JOIN deltallm_batch_item AS i ON i.item_id=r.item_id AND i.batch_id=r.batch_id
    JOIN deltallm_batch_job AS j ON j.batch_id=i.batch_id
    WHERE i.locked_by=r.worker_id AND i.claim_epoch=r.claim_epoch
      AND i.status='in_progress' AND i.lease_expires_at>NOW()
      AND j.created_by_api_key=r.api_key
      AND (
          r.checkpoint->'terminal' <> 'null'::jsonb OR (
              j.status='in_progress' AND j.cancel_requested_at IS NULL
              AND (j.expires_at IS NULL OR j.expires_at>NOW())
          )
      )
      AND (
          i.accounting_checkpoint IS NOT DISTINCT FROM r.expected
          OR (r.expected IS NOT NULL AND i.accounting_checkpoint=r.checkpoint)
          OR (
              r.checkpoint->'terminal' <> 'null'::jsonb
              AND (
                  i.accounting_checkpoint=r.checkpoint
                  OR i.accounting_checkpoint=jsonb_set(r.checkpoint,'{terminal}','null'::jsonb)
              )
          )
      )
    ORDER BY i.item_id FOR UPDATE OF i
), updated AS (
    UPDATE deltallm_batch_item AS i SET accounting_checkpoint=r.checkpoint
    FROM owned AS o JOIN requests AS r ON r.item_id=o.item_id
    WHERE i.item_id=o.item_id RETURNING i.item_id, i.batch_id, i.accounting_checkpoint
), enqueued AS (
    INSERT INTO deltallm_batch_completion_outbox (
        completion_id,batch_id,item_id,payload_json,status,attempt_count,max_attempts,
        next_attempt_at,created_at,updated_at
    )
    SELECT accounting_checkpoint->>'operation_id',batch_id,item_id,
           jsonb_build_object('native_accounting',accounting_checkpoint,
                              'billing_event_id',accounting_checkpoint->>'operation_id'),
           'queued',0,$2,NOW(),NOW(),NOW()
    FROM updated WHERE accounting_checkpoint->'terminal' <> 'null'::jsonb
    ON CONFLICT (item_id) DO NOTHING RETURNING item_id
)
SELECT u.item_id,
       CASE WHEN u.accounting_checkpoint->'terminal' = 'null'::jsonb THEN TRUE
            ELSE e.item_id IS NOT NULL OR (
                (o.payload_json->'native_accounting') - 'claim_epoch'
                = u.accounting_checkpoint - 'claim_epoch'
            )
       END AS delivery_saved
FROM updated AS u
LEFT JOIN enqueued AS e ON e.item_id=u.item_id
LEFT JOIN deltallm_batch_completion_outbox AS o ON o.item_id=u.item_id
"""


class BatchAccountingRepository:
    def __init__(self, db: Prisma) -> None:
        self._db = db

    async def write_many(
        self,
        writes: Sequence[BatchAccountingWrite],
        *,
        expires_at: float,
    ) -> None:
        if not writes:
            return
        if len(writes) > CHECKPOINT_BATCH_MAX_ITEMS or len(
            {write.claim.item_id for write in writes}
        ) != len(writes):
            raise BatchAccountingUnavailable()
        for write in writes:
            write.validate()
        document = json.dumps(
            [
                {
                    "item_id": write.claim.item_id,
                    "batch_id": write.claim.batch_id,
                    "worker_id": write.claim.worker_id,
                    "claim_epoch": write.claim.claim_epoch,
                    "api_key": write.claim.api_key,
                    "checkpoint": write.checkpoint.model_dump(mode="json"),
                    "expected": None
                    if write.expected is None
                    else write.expected.model_dump(mode="json"),
                }
                for write in writes
            ],
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(document.encode()) > CHECKPOINT_BATCH_MAX_BYTES:
            raise BatchAccountingUnavailable()
        await self._write(document, len(writes), expires_at=expires_at)

    async def _write(self, document: str, count: int, *, expires_at: float) -> None:
        remaining = self._budget(expires_at)
        try:
            async with asyncio.timeout(remaining):
                async with self._db.tx(
                    max_wait=timedelta(seconds=remaining),
                    timeout=timedelta(seconds=remaining),
                ) as tx:
                    await tx.query_raw(
                        "SELECT set_config('statement_timeout',$1,true), "
                        "set_config('lock_timeout',$1,true)",
                        f"{max(1, int(remaining * 1000))}ms",
                    )
                    rows = await tx.query_raw(
                        WRITE_CHECKPOINTS_SQL,
                        document,
                        COMPLETION_OUTBOX_MAX_ATTEMPTS,
                    )
                    if len(rows) != count or any(row["delivery_saved"] is not True for row in rows):
                        raise BatchAccountingUnavailable()
        except Exception:
            raise BatchAccountingUnavailable() from None

    async def mark_sent(
        self,
        completion_id: str,
        *,
        worker_id: str,
        attempt_count: int,
        expires_at: float,
    ) -> bool:
        return await execute_completion_transition(
            self._db,
            SENT_SQL,
            (completion_id, worker_id, attempt_count),
            attempt_count=attempt_count,
            expires_at=expires_at,
        )

    @staticmethod
    def _budget(expires_at: float) -> float:
        remaining = min(0.25, expires_at - asyncio.get_running_loop().time())
        if not math.isfinite(remaining) or remaining <= 0:
            raise BatchAccountingUnavailable()
        return remaining
