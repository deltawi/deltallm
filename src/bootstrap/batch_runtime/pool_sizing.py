from __future__ import annotations

import logging
from typing import Any

from src.batch.scheduling.modes import resolve_scheduler_modes_from_settings

logger = logging.getLogger(__name__)

# Claims, scheduler snapshots, the stale lease sweeper and finalization.
BATCH_BACKGROUND_DB_CONNECTIONS = 2
COMPLETION_OUTBOX_MAX_CONCURRENCY = 8


def completion_outbox_concurrency(general: Any) -> int:
    return min(
        COMPLETION_OUTBOX_MAX_CONCURRENCY,
        max(1, int(general.embeddings_batch_worker_concurrency or 4)),
    )


def _extra_slices_in_flight(general: Any) -> int:
    # A slice that has finished its items releases leftovers, refreshes its job
    # and may finalize it while the next slice's items already run. Only
    # work-slice scheduling runs more than one slice.
    if not resolve_scheduler_modes_from_settings(general).active_uses_work_slice:
        return 0
    slices = int(getattr(general, "embeddings_batch_worker_max_slices_in_flight", 1) or 1)
    return max(0, slices - 1)


def completion_outbox_sent_mark_concurrency(general: Any) -> int:
    """Native sent marks at once with write grouping on; 0 leaves them unlimited.

    Below the outbox's own concurrency, so marks that finish while the database
    is busy wait for a slot and are written together.
    """
    if not getattr(general, "embeddings_batch_write_grouping_enabled", False):
        return 0
    return max(1, completion_outbox_concurrency(general) // 4)


def batch_db_pool_size(general: Any, settings: object) -> int:
    return int(getattr(settings, "db_pool_size", None) or general.db_pool_size)


def batch_write_concurrency(general: Any, settings: object) -> int:
    """Billing checkpoint writes at once; 0 configured means half the pool."""
    configured = general.embeddings_batch_accounting_write_concurrency
    if configured > 0:
        return configured
    return max(1, batch_db_pool_size(general, settings) // 2)


def batch_result_save_concurrency(general: Any, settings: object) -> int:
    """Result saves at once with write grouping on; 0 leaves them unlimited."""
    if not getattr(general, "embeddings_batch_write_grouping_enabled", False):
        return 0
    if not (getattr(settings, "db_pool_size", None) or getattr(general, "db_pool_size", None)):
        return 0
    return batch_write_concurrency(general, settings)


def warn_if_batch_db_pool_is_small(general: Any, settings: object) -> int | None:
    """Warn when batch work on this process can need more connections than its pool.

    Batch items, completion outbox saves and background batch work all use the
    control pool (`db_pool_size`). Each running item can hold a connection at the
    same moment (lease renewal, billing checkpoint, result save), so a pool smaller
    than the sum makes items wait for or fail on a busy database. Returns the
    number of connections batch work can use when it is larger than the pool.
    """
    if not getattr(general, "embeddings_batch_worker_enabled", False):
        return None
    pool_size = getattr(settings, "db_pool_size", None) or getattr(general, "db_pool_size", None)
    if not pool_size:
        return None
    running_items = int(general.embeddings_batch_worker_concurrency)
    outbox = (
        completion_outbox_concurrency(general)
        if getattr(general, "embeddings_batch_completion_outbox_worker_enabled", False)
        else 0
    )
    background = BATCH_BACKGROUND_DB_CONNECTIONS + _extra_slices_in_flight(general)
    needed = running_items + outbox + background
    if needed <= int(pool_size):
        return None
    logger.warning(
        "batch db_pool_size %s is smaller than the %s connections batch work can use at once "
        "(%s running items + %s completion outbox + %s background); set db_pool_size to at "
        "least %s or lower embeddings_batch_worker_concurrency and add worker pods instead",
        pool_size,
        needed,
        running_items,
        outbox,
        background,
        needed,
    )
    return needed
