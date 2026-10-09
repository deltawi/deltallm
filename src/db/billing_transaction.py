from __future__ import annotations

import asyncio
import math
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, AsyncIterator

from src.billing.operation_reservation import BillingOperationUnavailable

if TYPE_CHECKING:
    from prisma import Prisma

DB_BUDGET_SECONDS = 0.25


@asynccontextmanager
async def billing_transaction(db: Prisma, expires_at: float) -> AsyncIterator[Prisma]:
    """A bounded transaction shared by durable billing intent owners."""
    now = asyncio.get_running_loop().time()
    if not math.isfinite(expires_at) or expires_at <= now:
        raise BillingOperationUnavailable()
    remaining = min(expires_at - now, DB_BUDGET_SECONDS)
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
    except Exception:
        raise BillingOperationUnavailable() from None
