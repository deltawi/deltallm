from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import Iterable
from typing import Any

from src.callbacks.base import CustomLogger
from src.callbacks.payload import StandardLoggingPayload

logger = logging.getLogger(__name__)

BUILTIN_CALLBACKS: dict[str, str] = {
    "prometheus": "src.callbacks.integrations.prometheus.PrometheusCallback",
    "langfuse": "src.callbacks.integrations.langfuse.LangfuseCallback",
    "otel": "src.callbacks.integrations.opentelemetry.OpenTelemetryCallback",
    "opentelemetry": "src.callbacks.integrations.opentelemetry.OpenTelemetryCallback",
    "s3": "src.callbacks.integrations.s3.S3Callback",
}


class CallbackManager:
    def __init__(self) -> None:
        self.success_callbacks: list[CustomLogger] = []
        self.failure_callbacks: list[CustomLogger] = []
        self.pre_call_hooks: list[CustomLogger] = []
        self.post_call_hooks: list[CustomLogger] = []
        self._tasks: set[asyncio.Task[Any]] = set()

    def register_callback(self, callback: str | CustomLogger | type[CustomLogger], callback_type: str = "success") -> None:
        handler = self._resolve_callback(callback)
        if callback_type in {"success", "both"}:
            self.success_callbacks.append(handler)
        if callback_type in {"failure", "both"}:
            self.failure_callbacks.append(handler)

        if handler.__class__.async_pre_call_hook is not CustomLogger.async_pre_call_hook:
            self.pre_call_hooks.append(handler)
        if (
            handler.__class__.async_post_call_success_hook is not CustomLogger.async_post_call_success_hook
            or handler.__class__.async_post_call_failure_hook is not CustomLogger.async_post_call_failure_hook
        ):
            self.post_call_hooks.append(handler)

    def load_from_settings(
        self,
        *,
        success_callbacks: Iterable[str] | None,
        failure_callbacks: Iterable[str] | None,
        callbacks: Iterable[str] | None,
        callback_settings: dict[str, dict[str, Any]] | None,
    ) -> None:
        settings = callback_settings or {}
        for name in success_callbacks or []:
            self._register_by_name(name, callback_type="success", callback_settings=settings)
        for name in failure_callbacks or []:
            self._register_by_name(name, callback_type="failure", callback_settings=settings)
        for name in callbacks or []:
            self._register_by_name(name, callback_type="both", callback_settings=settings)

    async def execute_pre_call_hooks(
        self,
        *,
        user_api_key_dict: dict[str, Any],
        cache: Any,
        data: dict[str, Any],
        call_type: str,
    ) -> dict[str, Any]:
        payload = data
        for handler in self.pre_call_hooks:
            try:
                maybe_payload = await handler.async_pre_call_hook(user_api_key_dict, cache, payload, call_type)
                if maybe_payload is not None:
                    payload = maybe_payload
            except Exception:
                logger.exception("callback pre-call hook failed", extra={"handler": handler.__class__.__name__})
        return payload

    async def execute_post_call_success_hooks(
        self,
        *,
        data: dict[str, Any],
        user_api_key_dict: dict[str, Any],
        response: Any,
    ) -> None:
        for handler in self.post_call_hooks:
            try:
                await handler.async_post_call_success_hook(data, user_api_key_dict, response)
            except Exception:
                logger.exception("callback post-call success hook failed", extra={"handler": handler.__class__.__name__})

    async def execute_post_call_failure_hooks(
        self,
        *,
        request_data: dict[str, Any],
        original_exception: Exception,
        user_api_key_dict: dict[str, Any],
    ) -> None:
        for handler in self.post_call_hooks:
            try:
                await handler.async_post_call_failure_hook(request_data, original_exception, user_api_key_dict)
            except Exception:
                logger.exception("callback post-call failure hook failed", extra={"handler": handler.__class__.__name__})

    def dispatch_success_callbacks(self, payload: StandardLoggingPayload) -> None:
        self._schedule(self.execute_success_callbacks(payload))

    def dispatch_failure_callbacks(self, payload: StandardLoggingPayload, exception: Exception) -> None:
        self._schedule(self.execute_failure_callbacks(payload, exception))

    async def execute_success_callbacks(self, payload: StandardLoggingPayload) -> None:
        for handler in self.success_callbacks:
            try:
                await handler.async_log_success_event(
                    kwargs=payload.model_dump(mode="json"),
                    response_obj=payload.response_obj,
                    start_time=payload.start_time,
                    end_time=payload.end_time,
                )
            except Exception:
                logger.exception("callback success execution failed", extra={"handler": handler.__class__.__name__})

    async def execute_failure_callbacks(self, payload: StandardLoggingPayload, exception: Exception) -> None:
        for handler in self.failure_callbacks:
            try:
                await handler.async_log_failure_event(
                    kwargs=payload.model_dump(mode="json"),
                    exception=exception,
                    start_time=payload.start_time,
                    end_time=payload.end_time,
                )
            except Exception:
                logger.exception("callback failure execution failed", extra={"handler": handler.__class__.__name__})

    async def shutdown(self) -> None:
        if not self._tasks:
            return
        tasks = list(self._tasks)
        self._tasks.clear()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _register_by_name(
        self,
        name: str,
        *,
        callback_type: str,
        callback_settings: dict[str, dict[str, Any]],
    ) -> None:
        try:
            handler = self._resolve_string_callback(name, callback_settings)
            self.register_callback(handler, callback_type=callback_type)
        except Exception as exc:
            logger.warning("failed to register callback", extra={"callback": name, "error": str(exc)})

    def _resolve_callback(self, callback: str | CustomLogger | type[CustomLogger]) -> CustomLogger:
        if isinstance(callback, CustomLogger):
            return callback
        if isinstance(callback, type) and issubclass(callback, CustomLogger):
            return callback()
        if isinstance(callback, str):
            return self._resolve_string_callback(callback, callback_settings={})
        raise ValueError(f"Invalid callback type: {type(callback)}")

    def _resolve_string_callback(self, callback: str, callback_settings: dict[str, dict[str, Any]]) -> CustomLogger:
        name = callback.strip()
        kwargs: dict[str, Any] = {}
        if name in BUILTIN_CALLBACKS:
            module_path, class_name = BUILTIN_CALLBACKS[name].rsplit(".", 1)
            kwargs = callback_settings.get(name, {})
        elif "." in name:
            module_path, class_name = name.rsplit(".", 1)
        else:
            raise ValueError(f"Unknown callback: {name}")

        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        if not isinstance(cls, type) or not issubclass(cls, CustomLogger):
            raise ValueError(f"Callback class must extend CustomLogger: {name}")
        return cls(**kwargs)

    def _schedule(self, coroutine: Any) -> None:
        try:
            task = asyncio.create_task(coroutine)
        except RuntimeError:
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
