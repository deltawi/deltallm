"""Bound each existing cleanup owner without losing reverse-order cleanup."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from contextlib import AsyncExitStack
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

from src.process_lifecycle import ProcessLifecycle
from src.metrics.lifecycle import cleanup_seconds, cleanup_events, forced_exit_intent

logger = logging.getLogger(__name__)
Phase = Literal["cancellation", "workers", "close"]


@dataclass
class ShutdownOwner:
    lifecycle: ProcessLifecycle
    pending: set[asyncio.Task[object]] = field(default_factory=set)
    failed: bool = False
    force_reported: bool = False

    def report_unfinished(self) -> None:
        self.failed = True
        if not self.force_reported:
            self.force_reported = True
            forced_exit_intent.inc()


shutdown_owner: ContextVar[ShutdownOwner | None] = ContextVar("shutdown_owner", default=None)


def retain_unfinished(tasks: Iterable[asyncio.Task[object]]) -> None:
    """Keep cancelled service-owned tasks visible until they actually finish."""
    owner = shutdown_owner.get()
    pending = {task for task in tasks if not task.done()}
    if pending and owner is not None and owner.lifecycle.draining:
        owner.report_unfinished()
        owner.pending.update(pending)
        for task in pending:
            task.add_done_callback(owner.pending.discard)


def cleanup_deadline(timeout: float, *, phase: Phase = "workers") -> float:
    deadline = asyncio.get_running_loop().time() + timeout
    owner = shutdown_owner.get()
    if owner is not None and owner.lifecycle.draining:
        deadlines = owner.lifecycle.begin_stopping()
        deadline = min(deadline, getattr(deadlines, phase))
    return deadline


async def run_cleanup(
    callback: Callable[[], Awaitable[object]], *, phase: Phase = "workers"
) -> None:
    await _bounded_close(callback, (), {}, phase)


def cleanup_timeout(timeout: float, *, phase: Phase = "workers") -> float:
    owner = shutdown_owner.get()
    if owner is None or not owner.lifecycle.draining:
        return max(0, timeout)
    return max(0, cleanup_deadline(timeout, phase=phase) - asyncio.get_running_loop().time())


class BoundedExitStack(AsyncExitStack):
    """Use existing stack ordering; each registered closer retains its own task."""

    def __init__(self, *, phase: Phase = "workers") -> None:
        super().__init__()
        self.phase = phase

    def push_async_callback(
        self,
        callback: Callable[..., Awaitable[Any]],
        /,
        *args: Any,
        cleanup_phase: Phase | None = None,
        **kwargs: Any,
    ) -> Callable[..., Awaitable[Any]]:
        super().push_async_callback(
            _bounded_close, callback, args, kwargs, cleanup_phase or self.phase
        )
        return callback


async def _bounded_close(
    callback: Callable[..., Awaitable[Any]],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    phase: Phase,
) -> None:
    owner = shutdown_owner.get()
    if owner is None:
        await callback(*args, **kwargs)
        return
    deadlines = owner.lifecycle.begin_stopping()
    if phase == "close":
        owner.lifecycle.enter_phase("close")
    started = asyncio.get_running_loop().time()
    task = asyncio.create_task(callback(*args, **kwargs), name=f"shutdown-{phase}")
    owner.pending.add(task)

    def observe(completed: asyncio.Task[object]) -> None:
        cleanup_seconds.labels(phase).observe(asyncio.get_running_loop().time() - started)
        owner.pending.discard(completed)
        if not completed.cancelled() and completed.exception() is not None:
            owner.failed = True
            logger.error("process cleanup failed phase=%s", phase)

    task.add_done_callback(observe)
    try:
        done, _ = await asyncio.wait(
            {task}, timeout=owner.lifecycle.remaining(getattr(deadlines, phase))
        )
        if not done:
            owner.report_unfinished()
            cleanup_events.labels(phase, "timeout").inc()
            task.cancel()
            logger.error("process cleanup timed out phase=%s", phase)
        elif task.cancelled():
            owner.report_unfinished()
            cleanup_events.labels(phase, "cancelled").inc()
        else:
            task.result()
            cleanup_events.labels(phase, "completed").inc()
    except asyncio.CancelledError:
        task.cancel()
        owner.report_unfinished()
        raise
