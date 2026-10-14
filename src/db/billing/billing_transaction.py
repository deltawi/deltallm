from __future__ import annotations

import asyncio
import math
from contextlib import asynccontextmanager
from datetime import timedelta
import logging
from typing import TYPE_CHECKING, AsyncIterator

from src.billing.charges.operation_reservation import BillingOperationUnavailable
from src.db.runtime.telemetry_acceptance import AcceptanceFailure, classify_acceptance_failure

if TYPE_CHECKING:
    from prisma import Prisma

logger = logging.getLogger(__name__)

DB_BUDGET_SECONDS = 0.25


@asynccontextmanager
async def billing_transaction(
    db: Prisma, expires_at: float, *, budget: float | None = None
) -> AsyncIterator[Prisma]:
    """A bounded transaction shared by durable billing intent owners."""
    now = asyncio.get_running_loop().time()
    if not math.isfinite(expires_at) or expires_at <= now:
        raise BillingOperationUnavailable()
    remaining = min(expires_at - now, DB_BUDGET_SECONDS if budget is None else budget)
    try:
        async with asyncio.timeout(remaining):
            async with db.tx(
                max_wait=timedelta(seconds=remaining), timeout=timedelta(seconds=remaining)
            ) as tx:
                await tx.query_raw(
                    "SELECT set_config('statement_timeout',$1,true), "
                    "set_config('lock_timeout',$1,true)",
                    f"{max(1, int(remaining * 1000))}ms",
                )
                yield tx
    except Exception as exc:
        reason = classify_acceptance_failure(exc)
        # Domain rejections raised by callers classify as unknown and stay quiet;
        # a lock, pool, deadline or connection failure is named for operators.
        if reason != AcceptanceFailure.UNKNOWN:
            logger.warning(
                "billing transaction failed reason=%s budget_ms=%d",
                reason.value,
                round(remaining * 1000),
            )
        raise BillingOperationUnavailable() from None
