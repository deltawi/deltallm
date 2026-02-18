from __future__ import annotations

import asyncio
from abc import ABC
from datetime import datetime
from typing import Any


class CustomLogger(ABC):
    """Base class for callback handlers."""

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
        await asyncio.to_thread(self.log_success_event, kwargs, response_obj, start_time, end_time)

    async def async_log_failure_event(
        self,
        kwargs: dict[str, Any],
        exception: Exception,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        await asyncio.to_thread(self.log_failure_event, kwargs, exception, start_time, end_time)

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
