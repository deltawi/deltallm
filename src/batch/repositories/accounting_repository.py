"""Use the existing primary batch pool for bounded execution recovery proofs."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
import json
import math
import random
from time import perf_counter
from typing import TYPE_CHECKING

from src.batch.accounting_checkpoint import (
    CHECKPOINT_BATCH_MAX_BYTES,
    CHECKPOINT_BATCH_MAX_ITEMS,
    BatchAccountingRejected,
    BatchAccountingUnavailable,
    BatchAccountingWrite,
)
from src.batch.worker_constants import COMPLETION_OUTBOX_MAX_ATTEMPTS
from src.batch.write_groups import WriteGroups
from src.metrics import (
    increment_batch_accounting_checkpoint_write_retry,
    observe_batch_accounting_checkpoint_write,
)
from src.batch.repositories.completion_outbox_fences import SENT_SQL, execute_completion_transition
from src.database_caller import with_database_caller

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
                  OR (i.accounting_checkpoint - 'released_attempts')
                     =jsonb_set(r.checkpoint,'{terminal}','null'::jsonb)
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


# Another try runs only when at least this much of the budget is left.
CHECKPOINT_RETRY_MIN_SECONDS = 0.25
# A full pool sheds a write at once, so one quick retry often lands in the
# same busy moment. Busy writes are tried up to this many times, pausing a
# little longer each time (half to all of 50, 100, 200, 400, 400 ms), all
# inside the same write budget.
CHECKPOINT_WRITE_MAX_TRIES = 6
CHECKPOINT_RETRY_FIRST_PAUSE_SECONDS = 0.05
CHECKPOINT_RETRY_MAX_PAUSE_SECONDS = 0.4
DEFAULT_MAX_CONCURRENT_WRITES = 8


class _CheckpointRejected(Exception):
    """The database answered, but did not save every proof: never retried."""


@dataclass(frozen=True, slots=True)
class _CheckpointBatch:
    """The proofs of one write_many call, ready to send alone or in a group."""

    # The JSON array's elements, without the brackets, so groups just join them.
    elements: str
    item_ids: frozenset[str]
    count: int
    expires_at: float


class BatchAccountingRepository:
    def __init__(
        self,
        db: Prisma,
        *,
        max_concurrent_writes: int = DEFAULT_MAX_CONCURRENT_WRITES,
        group_waiting_writes: bool = False,
    ) -> None:
        if type(max_concurrent_writes) is not int or max_concurrent_writes < 1:
            raise ValueError("Invalid batch checkpoint write concurrency")
        self._db = db
        # Extra writers wait here instead of timing out in the shared pool.
        # With grouping on, proofs that wait for a slot are saved together.
        self._writes: WriteGroups[_CheckpointBatch, bool] = WriteGroups(
            kind="accounting_checkpoint",
            limit=max_concurrent_writes,
            write=self._write_group,
            accepted=bool,
            max_rows=CHECKPOINT_BATCH_MAX_ITEMS,
            rows=lambda batch: batch.count,
            # One comma per extra member; the brackets are counted once below.
            size=lambda batch: len(batch.elements.encode()) + 1,
            max_size=CHECKPOINT_BATCH_MAX_BYTES - 2,
            conflicts=lambda group, batch: any(
                not member.item_ids.isdisjoint(batch.item_ids) for member in group
            ),
            enabled=group_waiting_writes,
        )

    @with_database_caller("batch_checkpoint")
    async def write_many(
        self,
        writes: Sequence[BatchAccountingWrite],
        *,
        expires_at: float,
    ) -> None:
        if not writes:
            return
        await self._write(self._batch(writes, expires_at=expires_at))

    @staticmethod
    def _batch(writes: Sequence[BatchAccountingWrite], *, expires_at: float) -> _CheckpointBatch:
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
        return _CheckpointBatch(
            elements=document[1:-1],
            item_ids=frozenset(write.claim.item_id for write in writes),
            count=len(writes),
            expires_at=expires_at,
        )

    async def _write(self, batch: _CheckpointBatch) -> None:
        started = perf_counter()
        try:
            async with asyncio.timeout(self._budget(batch.expires_at)):
                saved = await self._writes.run(batch)
        except Exception:
            observe_batch_accounting_checkpoint_write(
                outcome="unavailable", latency_seconds=perf_counter() - started
            )
            raise BatchAccountingUnavailable() from None
        if not saved:
            observe_batch_accounting_checkpoint_write(
                outcome="rejected", latency_seconds=perf_counter() - started
            )
            raise BatchAccountingRejected()
        observe_batch_accounting_checkpoint_write(
            outcome="saved", latency_seconds=perf_counter() - started
        )

    async def _write_group(self, batches: Sequence[_CheckpointBatch]) -> bool:
        """Save every proof of ``batches`` in one statement, or none of them."""
        document = "[" + ",".join(batch.elements for batch in batches) + "]"
        count = sum(batch.count for batch in batches)
        # No member waits longer than it would alone. A member with too little
        # time left for one attempt is about to time out on its own, so it does
        # not set the budget and take the others down with it.
        now = asyncio.get_running_loop().time()
        usable = [
            batch.expires_at
            for batch in batches
            if batch.expires_at - now >= CHECKPOINT_RETRY_MIN_SECONDS
        ]
        expires_at = min(usable) if usable else max(batch.expires_at for batch in batches)
        try:
            await self._write_with_retry(document, count, expires_at=expires_at)
        except _CheckpointRejected:
            return False
        return True

    async def _write_with_retry(self, document: str, count: int, *, expires_at: float) -> None:
        for tries in range(1, CHECKPOINT_WRITE_MAX_TRIES + 1):
            remaining = self._budget(expires_at)
            final = (
                tries == CHECKPOINT_WRITE_MAX_TRIES or remaining < 2 * CHECKPOINT_RETRY_MIN_SECONDS
            )
            try:
                # A try that hangs leaves half the budget for the next one.
                await self._write_once(
                    document, count, budget=remaining if final else remaining / 2
                )
                return
            except _CheckpointRejected:
                raise
            except Exception:
                if final:
                    raise
            # Same proofs again: a committed earlier try is accepted or
            # rejected, never doubled.
            increment_batch_accounting_checkpoint_write_retry()
            pause = min(
                CHECKPOINT_RETRY_MAX_PAUSE_SECONDS,
                CHECKPOINT_RETRY_FIRST_PAUSE_SECONDS * 2 ** (tries - 1),
            )
            # Never pause into the time the next try needs.
            spare = self._budget(expires_at) - CHECKPOINT_RETRY_MIN_SECONDS
            await asyncio.sleep(max(0.0, min(random.uniform(pause / 2, pause), spare)))

    async def _write_once(self, document: str, count: int, *, budget: float) -> None:
        async with asyncio.timeout(budget):
            async with self._db.tx(
                max_wait=timedelta(seconds=budget),
                timeout=timedelta(seconds=budget),
            ) as tx:
                await tx.query_raw(
                    "SELECT set_config('statement_timeout',$1,true), "
                    "set_config('lock_timeout',$1,true)",
                    f"{max(1, int(budget * 1000))}ms",
                )
                rows = await tx.query_raw(
                    WRITE_CHECKPOINTS_SQL,
                    document,
                    COMPLETION_OUTBOX_MAX_ATTEMPTS,
                )
                if len(rows) != count or any(row["delivery_saved"] is not True for row in rows):
                    raise _CheckpointRejected()

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
        remaining = expires_at - asyncio.get_running_loop().time()
        if not math.isfinite(remaining) or remaining <= 0:
            raise BatchAccountingUnavailable()
        return remaining
