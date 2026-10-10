"""Own completion lease transitions for legacy and native delivery."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import timedelta
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
    remaining = min(
        0.25,
        expires_at - asyncio.get_running_loop().time() if expires_at is not None else 0.25,
    )
    if not math.isfinite(remaining) or remaining <= 0:
        raise BatchCompletionTransitionUnavailable()
    try:
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
                return bool(await tx.query_raw(query, *parameters))
    except Exception:
        raise BatchCompletionTransitionUnavailable() from None
