"""Own completion lease transitions for legacy and native delivery."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import timedelta
import json
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prisma import Prisma


class BatchCompletionTransitionUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Batch completion claim is unavailable")


SENT_SQL = """
UPDATE deltallm_batch_completion_outbox
SET status='sent',last_error=NULL,locked_by=NULL,lease_expires_at=NULL,
    processed_at=NOW(),updated_at=NOW()
WHERE completion_id=$1 AND status='processing' AND locked_by=$2
  AND ($3::integer IS NULL OR (attempt_count=$3 AND lease_expires_at>NOW()))
RETURNING completion_id
"""
RETRY_SQL = """
UPDATE deltallm_batch_completion_outbox
SET status='retrying',last_error=$2,next_attempt_at=$3::timestamptz,
    locked_by=NULL,lease_expires_at=NULL,updated_at=NOW()
WHERE completion_id=$1 AND status='processing' AND locked_by=$4
  AND ($5::integer IS NULL OR (attempt_count=$5 AND lease_expires_at>NOW()))
RETURNING completion_id
"""
FAILED_SQL = """
UPDATE deltallm_batch_completion_outbox
SET status='failed',last_error=$2,locked_by=NULL,lease_expires_at=NULL,updated_at=NOW()
WHERE completion_id=$1 AND status='processing' AND locked_by=$3
  AND ($4::integer IS NULL OR (attempt_count=$4 AND lease_expires_at>NOW()))
RETURNING completion_id
"""
RENEW_SQL = """
UPDATE deltallm_batch_completion_outbox
SET lease_expires_at=NOW()+($3||' seconds')::interval,updated_at=NOW()
WHERE completion_id=$1 AND status='processing' AND locked_by=$2
  AND ($4::integer IS NULL OR (attempt_count=$4 AND lease_expires_at>NOW()))
RETURNING completion_id
"""
# SENT_SQL for many native marks at once: the same fence on every row, and each
# row answers for itself through the pairs it returns.
SENT_MANY_SQL = """
UPDATE deltallm_batch_completion_outbox o
SET status='sent',last_error=NULL,locked_by=NULL,lease_expires_at=NULL,
    processed_at=NOW(),updated_at=NOW()
FROM jsonb_to_recordset($1::jsonb) AS m(completion_id text,attempt_count integer)
WHERE o.completion_id=m.completion_id AND o.status='processing' AND o.locked_by=$2
  AND o.attempt_count=m.attempt_count AND o.lease_expires_at>NOW()
RETURNING o.completion_id,o.attempt_count
"""


async def execute_completion_transition(
    db: Prisma,
    query: str,
    parameters: Sequence[object],
    *,
    attempt_count: int | None,
    expires_at: float | None = None,
) -> bool:
    if query not in (SENT_SQL, RETRY_SQL, FAILED_SQL, RENEW_SQL):
        raise ValueError("Invalid completion transition")
    if attempt_count is None:
        # The old embedded-worker contract keeps its existing transaction owner.
        return bool(await db.query_raw(query, *parameters))
    if type(attempt_count) is not int or attempt_count < 1:
        raise BatchCompletionTransitionUnavailable()
    remaining = _budget(expires_at)
    try:
        return bool(await _fenced_rows(db, query, parameters, remaining=remaining))
    except Exception:
        raise BatchCompletionTransitionUnavailable() from None


async def execute_completion_sent_many(
    db: Prisma,
    marks: Sequence[tuple[str, int]],
    *,
    worker_id: str,
) -> frozenset[tuple[str, int]]:
    """Mark native rows sent in one statement; return the (id, attempt) pairs marked."""
    if not marks:
        return frozenset()
    if any(type(attempt) is not int or attempt < 1 for _, attempt in marks):
        raise BatchCompletionTransitionUnavailable()
    document = json.dumps(
        [{"completion_id": completion_id, "attempt_count": attempt} for completion_id, attempt in marks]
    )
    remaining = _budget(None)
    try:
        rows = await _fenced_rows(db, SENT_MANY_SQL, (document, worker_id), remaining=remaining)
    except Exception:
        raise BatchCompletionTransitionUnavailable() from None
    return frozenset((str(row["completion_id"]), int(row["attempt_count"])) for row in rows)


def _budget(expires_at: float | None) -> float:
    remaining = min(
        0.25,
        expires_at - asyncio.get_running_loop().time() if expires_at is not None else 0.25,
    )
    if not math.isfinite(remaining) or remaining <= 0:
        raise BatchCompletionTransitionUnavailable()
    return remaining


async def _fenced_rows(
    db: Prisma,
    query: str,
    parameters: Sequence[object],
    *,
    remaining: float,
) -> list[dict[str, object]]:
    async with asyncio.timeout(remaining):
        async with db.tx(
            max_wait=timedelta(seconds=remaining),
            timeout=timedelta(seconds=remaining),
        ) as tx:
            await tx.query_raw(
                "SELECT set_config('statement_timeout',$1,true), "
                "set_config('lock_timeout',$1,true)",
                f"{max(1, int(remaining * 1000))}ms",
            )
            return await tx.query_raw(query, *parameters)
