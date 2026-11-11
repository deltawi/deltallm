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
