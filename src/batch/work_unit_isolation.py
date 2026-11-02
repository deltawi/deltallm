from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


async def run_isolated_work_unit(
    work_unit: Callable[[], Awaitable[Any]],
    *,
    batch_id: str,
) -> None:
    """Run one item or chunk of a slice so its failure cannot stop the others.

    The items of a slice run in one TaskGroup, where an escaped error cancels
    every sibling, including provider calls in flight. A unit that gets here
    has already run its own cleanup; its items stay claimed by this worker and
    go back to pending through the slice release or the stale lease sweeper.
    """
    try:
        await work_unit()
    except Exception:
        logger.exception(
            "batch work unit failed; the rest of the slice continues batch_id=%s",
            batch_id,
        )
