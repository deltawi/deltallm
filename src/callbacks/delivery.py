"""Bounded best-effort delivery owned by CallbackManager."""

from __future__ import annotations

import asyncio
import logging
from contextvars import Context
from time import perf_counter

from src.blocking_work import BlockingWorkExecutor
from src.bounded_payload import PayloadCapacityExceeded, retained_size
from src.callbacks.base import CustomLogger
from src.callbacks.payload import StandardLoggingPayload
from src.callbacks.resources import CallbackResources
from src.metrics.request_work import callback_outcomes, work_bytes, work_in_flight, work_rejections
from src.request_work_settings import RequestWorkSettings

logger = logging.getLogger(__name__)


def integration_label(handler: CustomLogger) -> str:
    return {
        "src.callbacks.integrations.prometheus": "prometheus",
        "src.callbacks.integrations.langfuse": "langfuse",
        "src.callbacks.integrations.opentelemetry": "opentelemetry",
        "src.callbacks.integrations.s3": "s3",
    }.get(type(handler).__module__, "custom")


class CallbackDelivery:
    def __init__(self, settings: RequestWorkSettings) -> None:
        self.settings = settings
        self._tasks: set[asyncio.Task[None]] = set()
        self._slots = asyncio.Semaphore(settings.callback_max_concurrency)
        self._bytes = 0
        self._closed = False
        self.blocking = BlockingWorkExecutor(
            allocation="callback_sync",
            workers=settings.callback_max_concurrency,
            max_pending=settings.callback_max_concurrency,
            max_bytes=settings.callback_max_bytes,
            timeout_seconds=settings.callback_timeout_seconds,
            shutdown_seconds=settings.callback_shutdown_seconds,
        )
        self.resources = CallbackResources(self.blocking)

    @property
    def pending(self) -> int:
        return len(self._tasks)

    @property
    def retained_bytes(self) -> int:
        return self._bytes

    def dispatch(
        self, handlers: list[CustomLogger], payload: StandardLoggingPayload, *, failed: bool = False
    ) -> tuple[asyncio.Task[None], ...]:
        tasks = []
        for handler in tuple(handlers):
            reason = (
                "closed"
                if self._closed
                else "full"
                if self.pending >= self.settings.callback_max_pending
                else None
            )
            if reason:
                work_rejections.labels("callback", reason).inc()
                continue
            try:
                # Charge snapshots plus JSON/adapter copies. The sync executor
                # separately retains its own charge after this task times out.
                size = 3 * retained_size(payload, limit=self.settings.callback_max_payload_bytes)
            except PayloadCapacityExceeded:
                work_rejections.labels("callback", "payload").inc()
                continue
            if self._bytes + size > self.settings.callback_max_bytes:
                work_rejections.labels("callback", "bytes").inc()
                continue
            snapshot = payload.model_copy(deep=True)
            self._bytes += size
            work_bytes.labels("callback").inc(size)
            work_in_flight.labels("callback").inc()
            # Optional delivery must not retain an inference context or inherit
            # its expired deadline. It has its own finite delivery budget.
            task = asyncio.create_task(self._deliver(handler, snapshot, failed), context=Context())
            self._tasks.add(task)
            task.add_done_callback(
                lambda done, charge=size: self._finished(done, charge), context=Context()
            )
            tasks.append(task)
        return tuple(tasks)

    async def _deliver(
        self, handler: CustomLogger, payload: StandardLoggingPayload, failed: bool
    ) -> None:
        label = integration_label(handler)
        try:
            async with asyncio.timeout(self.settings.callback_timeout_seconds):
                async with self._slots:
                    kwargs = payload.model_dump(mode="json")
                    if failed:
                        # Never retain the request's exception/traceback graph or
                        # forward an unsanitized provider exception to integrations.
                        await handler.async_log_failure_event(
                            kwargs=kwargs,
                            exception=RuntimeError("Gateway request failed"),
                            start_time=payload.start_time,
                            end_time=payload.end_time,
                        )
                    else:
                        await handler.async_log_success_event(
                            kwargs=kwargs,
                            response_obj=payload.response_obj,
                            start_time=payload.start_time,
                            end_time=payload.end_time,
                        )
        except TimeoutError:
            callback_outcomes.labels(label, "timeout").inc()
        except asyncio.CancelledError:
            callback_outcomes.labels(label, "cancelled").inc()
            raise
        except Exception:
            callback_outcomes.labels(label, "failed").inc()
            logger.warning("optional callback delivery failed", extra={"integration": label})
        else:
            callback_outcomes.labels(label, "completed").inc()

    def _finished(self, task: asyncio.Task[None], size: int) -> None:
        self._tasks.discard(task)
        self._bytes -= size
        work_bytes.labels("callback").dec(size)
        work_in_flight.labels("callback").dec()
        if not task.cancelled():
            task.exception()

    async def shutdown(self) -> None:
        self._closed = True
        deadline = perf_counter() + self.settings.callback_shutdown_seconds
        if self._tasks:
            _, pending = await asyncio.wait(
                tuple(self._tasks), timeout=max(0, deadline - perf_counter())
            )
            for task in pending:
                task.cancel()
            # Capacity remains charged until each task actually exits, including
            # trusted extensions that suppress cancellation.
            await asyncio.sleep(0)
        await self.resources.shutdown(timeout=max(0, deadline - perf_counter()))
        self.blocking.shutdown_seconds = max(0, deadline - perf_counter())
        await self.blocking.shutdown()
