from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import Iterable
from typing import Any

from src.callbacks.base import CustomLogger
from src.callbacks.delivery import CallbackDelivery, integration_label
from src.metrics.request_work import callback_outcomes
from src.request_work_settings import RequestWorkSettings
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
    def __init__(self, settings: RequestWorkSettings | None = None) -> None:
        self.success_callbacks: list[CustomLogger] = []
        self.failure_callbacks: list[CustomLogger] = []
        self.pre_call_hooks: list[CustomLogger] = []
        self.post_call_hooks: list[CustomLogger] = []
        self.delivery = CallbackDelivery(settings or RequestWorkSettings())
        self._config_managed_handlers: set[int] = set()

    def register_callback(
        self, callback: str | CustomLogger | type[CustomLogger], callback_type: str = "success"
    ) -> None:
        if callback_type not in {"success", "failure", "both"}:
            raise ValueError("Callback type must be success, failure, or both")
        handler = self._resolve_callback(callback)
        targets = []
        if callback_type in {"success", "both"}:
            targets.append(self.success_callbacks)
        if callback_type in {"failure", "both"}:
            targets.append(self.failure_callbacks)

        if handler.__class__.async_pre_call_hook is not CustomLogger.async_pre_call_hook:
            targets.append(self.pre_call_hooks)
        if (
            handler.__class__.async_post_call_success_hook
            is not CustomLogger.async_post_call_success_hook
            or handler.__class__.async_post_call_failure_hook
            is not CustomLogger.async_post_call_failure_hook
        ):
            targets.append(self.post_call_hooks)
        if any(len(items) >= 32 for items in targets):
            raise ValueError("At most 32 callback handlers may be registered per outcome")
        self.delivery.resources.bind(handler)
        for items in targets:
            items.append(handler)

    def load_from_settings(
        self,
        *,
        success_callbacks: Iterable[str] | None,
        failure_callbacks: Iterable[str] | None,
        callbacks: Iterable[str] | None,
        callback_settings: dict[str, dict[str, Any]] | None,
    ) -> None:
        self._remove_config_managed_callbacks()
        settings = callback_settings or {}
        success_names = _unique_callback_names(success_callbacks)
        failure_names = _unique_callback_names(failure_callbacks)
        both_names = _unique_callback_names(callbacks)

        # A callback present in ``callbacks`` already receives both outcomes.
        # Do not instantiate it again from the outcome-specific lists.
        both_keys = {_callback_identity(name) for name in both_names}
        for name in success_names:
            if _callback_identity(name) in both_keys:
                continue
            self._register_by_name(name, callback_type="success", callback_settings=settings)
        for name in failure_names:
            if _callback_identity(name) in both_keys:
                continue
            self._register_by_name(name, callback_type="failure", callback_settings=settings)
        for name in both_names:
            self._register_by_name(name, callback_type="both", callback_settings=settings)

    def _remove_config_managed_callbacks(self) -> None:
        if not self._config_managed_handlers:
            return
        managed = self._config_managed_handlers
        handlers = {
            id(item): item
            for item in self.success_callbacks
            + self.failure_callbacks
            + self.pre_call_hooks
            + self.post_call_hooks
            if id(item) in managed
        }
        self.success_callbacks = [
            item for item in self.success_callbacks if id(item) not in managed
        ]
        self.failure_callbacks = [
            item for item in self.failure_callbacks if id(item) not in managed
        ]
        self.pre_call_hooks = [item for item in self.pre_call_hooks if id(item) not in managed]
        self.post_call_hooks = [item for item in self.post_call_hooks if id(item) not in managed]
        managed.clear()
        for handler in handlers.values():
            self.delivery.resources.retire(handler)

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
                async with asyncio.timeout(self.delivery.settings.callback_timeout_seconds):
                    maybe_payload = await handler.async_pre_call_hook(
                        user_api_key_dict, cache, payload, call_type
                    )
                if maybe_payload is not None:
                    payload = maybe_payload
            except Exception:
                callback_outcomes.labels(integration_label(handler), "hook_failed").inc()
                logger.warning("callback pre-call hook failed")
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
                async with asyncio.timeout(self.delivery.settings.callback_timeout_seconds):
                    await handler.async_post_call_success_hook(data, user_api_key_dict, response)
            except Exception:
                callback_outcomes.labels(integration_label(handler), "hook_failed").inc()
                logger.warning("callback post-call success hook failed")

    async def execute_post_call_failure_hooks(
        self,
        *,
        request_data: dict[str, Any],
        original_exception: Exception,
        user_api_key_dict: dict[str, Any],
    ) -> None:
        for handler in self.post_call_hooks:
            try:
                async with asyncio.timeout(self.delivery.settings.callback_timeout_seconds):
                    await handler.async_post_call_failure_hook(
                        request_data, original_exception, user_api_key_dict
                    )
            except Exception:
                callback_outcomes.labels(integration_label(handler), "hook_failed").inc()
                logger.warning("callback post-call failure hook failed")

    def dispatch_success_callbacks(self, payload: StandardLoggingPayload) -> None:
        self.delivery.dispatch(self.success_callbacks, payload)

    def dispatch_failure_callbacks(
        self, payload: StandardLoggingPayload, exception: Exception
    ) -> None:
        self.delivery.dispatch(self.failure_callbacks, payload, failed=True)

    async def execute_success_callbacks(self, payload: StandardLoggingPayload) -> None:
        tasks = self.delivery.dispatch(self.success_callbacks, payload)
        if tasks:
            await asyncio.gather(*tasks)

    async def execute_failure_callbacks(
        self, payload: StandardLoggingPayload, exception: Exception
    ) -> None:
        tasks = self.delivery.dispatch(self.failure_callbacks, payload, failed=True)
        if tasks:
            await asyncio.gather(*tasks)

    async def shutdown(self) -> None:
        await self.delivery.shutdown()

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
            self._config_managed_handlers.add(id(handler))
        except Exception as exc:
            logger.warning(
                "failed to register callback", extra={"callback": name, "error": str(exc)}
            )

    def _resolve_callback(self, callback: str | CustomLogger | type[CustomLogger]) -> CustomLogger:
        if isinstance(callback, CustomLogger):
            return callback
        if isinstance(callback, type) and issubclass(callback, CustomLogger):
            return callback()
        if isinstance(callback, str):
            return self._resolve_string_callback(callback, callback_settings={})
        raise ValueError(f"Invalid callback type: {type(callback)}")

    def _resolve_string_callback(
        self, callback: str, callback_settings: dict[str, dict[str, Any]]
    ) -> CustomLogger:
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


def _callback_identity(name: str) -> str:
    normalized = str(name or "").strip()
    return BUILTIN_CALLBACKS.get(normalized, normalized)


def _unique_callback_names(names: Iterable[str] | None) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw_name in names or []:
        name = str(raw_name or "").strip()
        identity = _callback_identity(name)
        if not name or identity in seen:
            continue
        seen.add(identity)
        unique.append(name)
    return unique
