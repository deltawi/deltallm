from __future__ import annotations

import asyncio
import contextlib
import logging
from asyncio import Task, create_task
from typing import Any

import httpx

from src.bootstrap.batch_runtime.runtime import BatchRuntime

logger = logging.getLogger(__name__)

# Upper bound on how long shutdown waits for an in-flight process_once
# iteration to drain before hard-cancelling the worker task. The executor and
# webhook workers receive the conservative timeout because they may be doing
# network or artifact I/O when shutdown starts.
WORKER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS = 30.0
WORKER_SHUTDOWN_CANCEL_CLEANUP_TIMEOUT_SECONDS = 5.0
WEBHOOK_TRANSPORT_CLOSE_TIMEOUT_SECONDS = 1.0


async def drain_worker_task(
    task: Task[None],
    *,
    label: str,
    timeout: float,
) -> None:
    """Drain one stopped worker, falling back to bounded cancellation."""

    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        return
    except asyncio.TimeoutError:
        logger.warning(
            "%s drain timed out after %.1fs; cancelling in-flight iteration",
            label,
            timeout,
        )
    except asyncio.CancelledError:
        current_task = asyncio.current_task()
        if task.cancelled() and (current_task is None or current_task.cancelling() == 0):
            return
        raise
    except Exception:
        logger.exception("%s drain raised", label)
        return
    pending_tasks = await _cancel_worker_tasks_bounded(
        (task,),
        timeout=WORKER_SHUTDOWN_CANCEL_CLEANUP_TIMEOUT_SECONDS,
    )
    if pending_tasks:
        logger.warning(
            "%s cancellation cleanup timed out after %.1fs",
            label,
            WORKER_SHUTDOWN_CANCEL_CLEANUP_TIMEOUT_SECONDS,
        )


def _consume_task_result(task: Task[Any]) -> None:
    with contextlib.suppress(BaseException):
        task.result()


async def _cancel_worker_tasks_bounded(
    tasks: tuple[Task[None], ...],
    *,
    timeout: float,
) -> tuple[Task[None], ...]:
    active_tasks: list[Task[None]] = []
    for task in tasks:
        if task.done():
            _consume_task_result(task)
        else:
            active_tasks.append(task)
    active = tuple(active_tasks)
    if not active:
        return ()
    for task in active:
        task.cancel()
    try:
        done, pending = await asyncio.wait(
            active,
            timeout=max(0.0, float(timeout)),
        )
    except asyncio.CancelledError:
        for task in active:
            if task.done():
                _consume_task_result(task)
            else:
                task.cancel()
                task.add_done_callback(_consume_task_result)
        raise
    for task in done:
        _consume_task_result(task)
    pending_tasks = tuple(pending)
    for task in pending_tasks:
        task.cancel()
        task.add_done_callback(_consume_task_result)
    return pending_tasks


async def _close_webhook_transport_bounded(
    transport: httpx.AsyncBaseTransport | None,
    *,
    timeout: float,
) -> None:
    if transport is None:
        return
    close_task = create_task(transport.aclose())
    try:
        done, _pending = await asyncio.wait(
            {close_task},
            timeout=max(0.0, float(timeout)),
        )
    except asyncio.CancelledError:
        close_task.cancel()
        close_task.add_done_callback(_consume_task_result)
        raise
    if close_task in done:
        try:
            close_task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("batch webhook transport close failed during shutdown")
        return

    close_task.cancel()
    close_task.add_done_callback(_consume_task_result)
    logger.warning("batch webhook transport close timed out during shutdown")


async def shutdown_batch_runtime(runtime: BatchRuntime) -> None:
    worker_specs = (
        (
            runtime.worker,
            runtime.worker_task,
            "batch worker",
            WORKER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS,
        ),
        (
            runtime.completion_outbox_worker,
            runtime.completion_outbox_task,
            "batch completion outbox worker",
            5.0,
        ),
        (
            runtime.webhook_outbox_worker,
            runtime.webhook_outbox_task,
            "batch webhook outbox worker",
            WORKER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS,
        ),
        (
            runtime.webhook_observability_worker,
            runtime.webhook_observability_task,
            "batch webhook observability worker",
            5.0,
        ),
        (runtime.gc_worker, runtime.gc_task, "batch gc worker", 5.0),
        (
            runtime.scheduler_backfill_worker,
            runtime.scheduler_backfill_task,
            "batch scheduler backfill worker",
            5.0,
        ),
        (
            runtime.stale_lease_sweeper_worker,
            runtime.stale_lease_sweeper_task,
            "batch stale lease sweeper worker",
            5.0,
        ),
        (
            runtime.create_session_cleanup_worker,
            runtime.create_session_cleanup_task,
            "batch create-session cleanup worker",
            5.0,
        ),
    )

    # Stop every claim loop before awaiting any one worker. This prevents later
    # workers from claiming fresh leases while an earlier worker is draining.
    for worker, _task, _label, _timeout in worker_specs:
        if worker is not None:
            worker.stop()

    drain_coroutines = [
        drain_worker_task(task, label=label, timeout=timeout)
        for _worker, task, label, timeout in worker_specs
        if task is not None
    ]
    worker_tasks = tuple(
        task for _worker, task, _label, _timeout in worker_specs if task is not None
    )
    try:
        if drain_coroutines:
            await asyncio.gather(*drain_coroutines)
    except asyncio.CancelledError:
        pending_tasks: tuple[Task[None], ...] = ()
        try:
            pending_tasks = await _cancel_worker_tasks_bounded(
                worker_tasks,
                timeout=WORKER_SHUTDOWN_CANCEL_CLEANUP_TIMEOUT_SECONDS,
            )
            if pending_tasks:
                logger.warning(
                    "batch worker cancellation cleanup timed out pending_tasks=%s",
                    len(pending_tasks),
                )
        finally:
            await _close_webhook_transport_bounded(
                runtime.webhook_transport,
                timeout=WEBHOOK_TRANSPORT_CLOSE_TIMEOUT_SECONDS,
            )
        for task in pending_tasks:
            if not task.done():
                task.cancel()
        raise
    await _close_webhook_transport_bounded(
        runtime.webhook_transport,
        timeout=WEBHOOK_TRANSPORT_CLOSE_TIMEOUT_SECONDS,
    )
