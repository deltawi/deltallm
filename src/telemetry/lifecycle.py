from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class WorkerState(StrEnum):
    DISABLED = "disabled"
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class WorkerHealth:
    state: WorkerState
    detail: str | None = None

    @property
    def ready(self) -> bool:
        return self.state in {WorkerState.DISABLED, WorkerState.READY}


async def wait_for_startup(
    *,
    started: asyncio.Event,
    task: asyncio.Task[object],
    timeout_seconds: float,
    worker_name: str,
) -> None:
    """Wait until a worker enters its loop or terminates during startup."""

    started_waiter = asyncio.create_task(started.wait())
    try:
        done, _ = await asyncio.wait(
            {started_waiter, task},
            timeout=max(0.0, timeout_seconds),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if started_waiter in done and started_waiter.result():
            return
        if task in done:
            task.result()
            raise RuntimeError(f"{worker_name} stopped during startup")
        raise TimeoutError(f"{worker_name} did not become ready before its startup deadline")
    finally:
        if not started_waiter.done():
            started_waiter.cancel()
        with suppress(asyncio.CancelledError):
            await started_waiter


async def stop_tasks_before_deadline(
    tasks: Iterable[asyncio.Task[object] | None],
    *,
    deadline: float,
    cancel_first: bool = False,
) -> bool:
    """Stop owned tasks without waiting past a caller-owned monotonic deadline."""

    pending = {task for task in tasks if task is not None and not task.done()}
    if cancel_first:
        for task in pending:
            task.cancel()
    if pending:
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        _, pending = await asyncio.wait(pending, timeout=remaining)
    if pending and not cancel_first:
        for task in pending:
            task.cancel()
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        if remaining > 0:
            _, pending = await asyncio.wait(pending, timeout=remaining)
    for task in pending:
        task.cancel()
        task.add_done_callback(_observe_task_result)
    return not pending


def task_failure_detail(task: asyncio.Task[object] | None) -> str | None:
    if task is None or not task.done():
        return None
    if task.cancelled():
        return "task cancelled unexpectedly"
    exception = task.exception()
    if exception is None:
        return "task stopped unexpectedly"
    return f"{type(exception).__name__}: {exception}"


def _observe_task_result(task: asyncio.Task[object]) -> None:
    with suppress(asyncio.CancelledError, Exception):
        task.exception()
