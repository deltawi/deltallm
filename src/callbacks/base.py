from __future__ import annotations

from abc import ABC
from _thread import LockType
from collections.abc import Callable
from datetime import datetime
from functools import partial
from typing import Any, TypeVar

from src.blocking_work import BlockingWorkExecutor, WorkUnavailableError
from src.bounded_payload import retained_size

T = TypeVar("T")


class CustomLogger(ABC):
    """Base class for callback handlers."""

    blocking_executor: BlockingWorkExecutor | None = None
    blocking_lock: LockType | None = None
    retired: bool = False

    def close(self) -> None:
        """Release integration resources under the owned blocking allocation."""

    async def run_blocking(self, function: Callable[..., T], *args: object) -> T:
        executor = self.blocking_executor
        if executor is None:
            raise WorkUnavailableError()
        # Drop exception objects before sizing: callers pass a safe isolated
        # failure without the original request traceback.
        values = tuple("request failed" if isinstance(arg, Exception) else arg for arg in args)
        size = 3 * retained_size(values, limit=executor.max_bytes // 3)
        work = partial(function, *args)

        def serialized() -> T:
            if self.blocking_lock is None:
                raise WorkUnavailableError()
            # Client creation and synchronous SDK use have one owner per handler.
            with self.blocking_lock:
                if self.retired:
                    raise WorkUnavailableError()
                return work()

        return await executor.run(serialized, payload_bytes=size)

    def log_pre_api_call(self, model: str, messages: list[Any], kwargs: dict[str, Any]) -> None:
        del model, messages, kwargs

    def log_post_api_call(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        del kwargs, response_obj, start_time, end_time

    def log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        del kwargs, response_obj, start_time, end_time

    def log_failure_event(
        self,
        kwargs: dict[str, Any],
        exception: Exception,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        del kwargs, exception, start_time, end_time

    async def async_log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        await self.run_blocking(self.log_success_event, kwargs, response_obj, start_time, end_time)

    async def async_log_failure_event(
        self,
        kwargs: dict[str, Any],
        exception: Exception,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        await self.run_blocking(self.log_failure_event, kwargs, exception, start_time, end_time)

    async def async_log_stream_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        del kwargs, response_obj, start_time, end_time

    async def async_pre_call_hook(
        self,
        user_api_key_dict: dict[str, Any],
        cache: Any,
        data: dict[str, Any],
        call_type: str,
    ) -> dict[str, Any] | None:
        del user_api_key_dict, cache, call_type
        return data

    async def async_post_call_success_hook(
        self,
        data: dict[str, Any],
        user_api_key_dict: dict[str, Any],
        response: Any,
    ) -> None:
        del data, user_api_key_dict, response

    async def async_post_call_failure_hook(
        self,
        request_data: dict[str, Any],
        original_exception: Exception,
        user_api_key_dict: dict[str, Any],
    ) -> None:
        del request_data, original_exception, user_api_key_dict
