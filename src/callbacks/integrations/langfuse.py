from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any

from src.callbacks.base import CustomLogger

try:
    from langfuse import Langfuse

    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False
    Langfuse = Any  # type: ignore[assignment,misc]


class LangfuseCallback(CustomLogger):
    def __init__(
        self,
        public_key: str | None = None,
        secret_key: str | None = None,
        host: str = "https://cloud.langfuse.com",
        release: str | None = None,
    ) -> None:
        if not LANGFUSE_AVAILABLE:
            raise ImportError("langfuse package required. Install with: pip install langfuse")

        self.public_key = public_key or os.getenv("LANGFUSE_PUBLIC_KEY")
        self.secret_key = secret_key or os.getenv("LANGFUSE_SECRET_KEY")
        self.host = host
        self.release = release
        self._client: Langfuse | None = None

    @property
    def client(self) -> Langfuse:
        if self._client is None:
            self._client = Langfuse(
                public_key=self.public_key,
                secret_key=self.secret_key,
                host=self.host,
                release=self.release,
            )
        return self._client

    async def async_log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        await asyncio.to_thread(self._log_success, kwargs, response_obj, start_time, end_time)

    async def async_log_failure_event(
        self,
        kwargs: dict[str, Any],
        exception: Exception,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        await asyncio.to_thread(self._log_failure, kwargs, exception, start_time, end_time)

    def _log_success(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        metadata = dict(kwargs.get("metadata") or {})
        trace = self.client.trace(
            id=metadata.get("trace_id") or metadata.get("langfuse_trace_id"),
            name=str(metadata.get("generation_name") or kwargs.get("call_type") or "completion"),
            user_id=kwargs.get("user"),
            metadata={
                "model": kwargs.get("model"),
                "api_key": kwargs.get("api_key"),
                "team_id": kwargs.get("team_id"),
            },
        )

        output = None
        if isinstance(response_obj, dict):
            choices = response_obj.get("choices")
            if isinstance(choices, list) and choices:
                output = choices[0].get("message")

        usage = kwargs.get("usage") or {}
        trace.generation(
            name=str(metadata.get("generation_name") or "completion"),
            model=kwargs.get("model"),
            input=kwargs.get("messages"),
            output=output,
            start_time=start_time,
            end_time=end_time,
            usage={
                "input": int(usage.get("prompt_tokens") or 0),
                "output": int(usage.get("completion_tokens") or 0),
                "total": int(usage.get("total_tokens") or 0),
                "unit": "TOKENS",
                "total_cost": float(kwargs.get("response_cost") or 0.0),
            },
            metadata={
                "cache_hit": bool(kwargs.get("cache_hit") or False),
                "api_provider": kwargs.get("api_provider"),
                "stream": bool(kwargs.get("stream") or False),
            },
        )
        self.client.flush()

    def _log_failure(
        self,
        kwargs: dict[str, Any],
        exception: Exception,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        metadata = dict(kwargs.get("metadata") or {})
        trace = self.client.trace(
            id=metadata.get("trace_id") or metadata.get("langfuse_trace_id"),
            name=str(metadata.get("generation_name") or "completion"),
            user_id=kwargs.get("user"),
        )
        trace.generation(
            name="completion",
            model=kwargs.get("model"),
            input=kwargs.get("messages"),
            start_time=start_time,
            end_time=end_time,
            level="ERROR",
            status_message=str(exception),
            metadata={"error_type": exception.__class__.__name__},
        )
        self.client.flush()
