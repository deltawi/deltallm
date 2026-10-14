"""Post-commit release of shared telemetry queue capacity."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

_RELEASE_RETRY_DELAYS_SECONDS = (0.0, 0.05, 0.25)
# Strong references keep releases alive when their caller is cancelled.
_RELEASES: set[asyncio.Task] = set()


async def release_after_commit(
    release: Callable[[], Awaitable[None]],
    *,
    queue: str,
    on_failure: Callable[[], None],
) -> None:
    """Run a capacity release that must not be lost to the caller's cancellation.

    The worker batch has already committed its completions, so a cancelled caller
    still releases their capacity. The release never raises: a lost release leaves
    the counter high, which only rejects admission early, and the next startup
    reconciliation recounts the queue.
    """
    task = asyncio.get_running_loop().create_task(_release(release, queue, on_failure))
    _RELEASES.add(task)
    task.add_done_callback(_RELEASES.discard)
    await asyncio.shield(task)


async def _release(
    release: Callable[[], Awaitable[None]],
    queue: str,
    on_failure: Callable[[], None],
) -> None:
    for delay in _RELEASE_RETRY_DELAYS_SECONDS:
        if delay:
            await asyncio.sleep(delay)
        try:
            await release()
            return
        except Exception:
            continue
    on_failure()
    logger.warning(
        "telemetry capacity release failed queue=%s; startup reconciliation will recount it",
        queue,
    )
