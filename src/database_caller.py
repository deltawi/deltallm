"""Name the code path that holds a database slot, for bounded slot metrics.

A caller is a fixed class, never a tenant, request, batch or item. Code that
uses a pooled Prisma client marks its path with ``database_caller(...)``. The
allocation owner reads the current class when it gives out a slot, so the slot
metrics show which work holds, waits for, or is refused a connection. Work
that is not marked counts as ``other``.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal, TypeVar, cast, get_args

DatabaseCaller = Literal[
    "other",
    "readiness",
    # API request path.
    "auth",
    "key_last_used",
    "spend",
    "audit",
    # Batch worker.
    "batch_claim",
    "batch_item",
    "batch_checkpoint",
    "batch_result_save",
    "batch_requeue",
    "batch_lease",
    "batch_progress",
    "batch_finalize",
    "batch_outbox",
    "batch_webhook",
    "batch_maintenance",
    # Shared background workers.
    "cache_invalidation",
    "organization_deletion",
]
DATABASE_CALLERS: frozenset[str] = frozenset(get_args(DatabaseCaller))

# On a process that runs the batch worker, callers that wait for a slot of the
# control pool get it in this order (lower first). Work that is not listed
# has CLAIM_PRIORITY. API requests come first: a client waits on them, with a
# short deadline. Saving an answer that the provider already gave, or giving
# it back to the queue, comes next. A lost save turns paid work into a failed
# item. Billing checkpoints and lease renewals follow: a lost lease gives the
# item to another worker and the answer is lost too. Then the completion
# outbox, then claims and other batch work.
REQUEST_PRIORITY = 0
PAID_RESULT_PRIORITY = 1
BILLING_PRIORITY = 2
OUTBOX_PRIORITY = 3
CLAIM_PRIORITY = 4
BACKGROUND_PRIORITY = 5
_CALLER_PRIORITIES: dict[str, int] = {
    "auth": REQUEST_PRIORITY,
    "spend": REQUEST_PRIORITY,
    "audit": REQUEST_PRIORITY,
    "batch_result_save": PAID_RESULT_PRIORITY,
    "batch_requeue": PAID_RESULT_PRIORITY,
    "batch_checkpoint": BILLING_PRIORITY,
    "batch_lease": BILLING_PRIORITY,
    "batch_outbox": OUTBOX_PRIORITY,
    # Background work also holds at most a few slots at a time.
    "batch_webhook": BACKGROUND_PRIORITY,
    "batch_maintenance": BACKGROUND_PRIORITY,
    "cache_invalidation": BACKGROUND_PRIORITY,
    "organization_deletion": BACKGROUND_PRIORITY,
}


def caller_priority(caller: str) -> int:
    """Order of ``caller`` in a prioritized pool; lower gets a slot first."""
    return _CALLER_PRIORITIES.get(caller, CLAIM_PRIORITY)


def background_slots(connections: int) -> int:
    """Slots that background callers may hold at the same time in a pool."""
    return max(1, min(4, int(connections) // 8))


F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

_current: ContextVar[str] = ContextVar("deltallm_database_caller", default="other")


def current_database_caller() -> str:
    return _current.get()


@contextmanager
def database_caller(name: DatabaseCaller) -> Iterator[None]:
    """Mark the database work inside this block, and tasks it starts, as ``name``."""
    if name not in DATABASE_CALLERS:
        raise ValueError(f"Unknown database caller: {name}")
    token = _current.set(name)
    try:
        yield
    finally:
        _current.reset(token)


def with_database_caller(name: DatabaseCaller) -> Callable[[F], F]:
    """Decorate an async function so its database work counts as ``name``."""
    if name not in DATABASE_CALLERS:
        raise ValueError(f"Unknown database caller: {name}")

    def decorate(function: F) -> F:
        @functools.wraps(function)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            with database_caller(name):
                return await function(*args, **kwargs)

        return cast(F, wrapper)

    return decorate
