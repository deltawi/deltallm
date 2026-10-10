from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, TypeVar

from src.metrics import (
    increment_prompt_singleflight_outcome,
    set_prompt_singleflight_inflight,
)
from src.models.errors import ServiceUnavailableError
from src.telemetry.lifecycle import stop_tasks_before_deadline

T = TypeVar("T")


class PromptSingleflightOverloadedError(ServiceUnavailableError):
    error_type = "prompt_resolution_overloaded"
    message = "Prompt resolution is temporarily at capacity"

    def __init__(self) -> None:
        super().__init__(code="prompt_resolution_overloaded")


class PromptSingleflightTimeoutError(ServiceUnavailableError):
    error_type = "prompt_resolution_timeout"
    message = "Prompt resolution timed out"

    def __init__(self) -> None:
        super().__init__(code="prompt_resolution_timeout")


class PromptSingleflightClosedError(ServiceUnavailableError):
    error_type = "prompt_resolution_unavailable"
    message = "Prompt resolution is unavailable while the service is stopping"

    def __init__(self) -> None:
        super().__init__(code="prompt_resolution_unavailable")


class PromptSingleflight:
    """Bounded owner for per-process prompt cold-load tasks."""

    def __init__(self, *, max_keys: int, timeout_seconds: float) -> None:
        self.max_keys = max(1, int(max_keys))
        self.timeout_seconds = max(0.01, float(timeout_seconds))
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._closed = False

    @property
    def size(self) -> int:
        return len(self._tasks)

    @property
    def tasks(self) -> dict[str, asyncio.Task[Any]]:
        return dict(self._tasks)

    async def run(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        if self._closed:
            increment_prompt_singleflight_outcome(outcome="closed")
            raise PromptSingleflightClosedError()

        task = self._tasks.get(key)
        if task is None:
            if len(self._tasks) >= self.max_keys:
                increment_prompt_singleflight_outcome(outcome="overloaded")
                raise PromptSingleflightOverloadedError()
            task = asyncio.create_task(self._run_owned(key, factory))
            self._tasks[key] = task
            set_prompt_singleflight_inflight(len(self._tasks))
            task.add_done_callback(self._observe_result)
        return await asyncio.shield(task)

    async def shutdown(self, *, timeout_seconds: float | None = None) -> None:
        self._closed = True
        deadline = asyncio.get_running_loop().time() + max(
            0.0,
            self.timeout_seconds if timeout_seconds is None else float(timeout_seconds),
        )
        await stop_tasks_before_deadline(list(self._tasks.values()), deadline=deadline)
        self._tasks.clear()
        set_prompt_singleflight_inflight(0)

    async def _run_owned(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        current = asyncio.current_task()
        outcome = "success"
        try:
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    return await factory()
            except TimeoutError as exc:
                outcome = "timeout"
                raise PromptSingleflightTimeoutError() from exc
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            except Exception:
                outcome = "error"
                raise
        finally:
            if current is not None and self._tasks.get(key) is current:
                self._tasks.pop(key, None)
            set_prompt_singleflight_inflight(len(self._tasks))
            increment_prompt_singleflight_outcome(outcome=outcome)

    @staticmethod
    def _observe_result(task: asyncio.Task[Any]) -> None:
        with suppress(asyncio.CancelledError, Exception):
            task.exception()
