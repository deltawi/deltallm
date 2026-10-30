from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from src.callbacks.base import CustomLogger
from src.callbacks.integrations.otel_export import BoundedBatchSpanProcessor

try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.trace import Status, StatusCode
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False

# OpenTelemetry GenAI semantic-convention operation names.
_GENAI_OPERATIONS = {
    "completion": "chat",
    "embedding": "embeddings",
    "mcp_tool": "execute_tool",
}


class OpenTelemetryCallback(CustomLogger):
    def __init__(
        self,
        endpoint: str | None = None,
        headers: dict[str, str] | None = None,
        service_name: str = "deltallm",
        max_queue_size: int = 2048,
        max_export_batch_size: int = 512,
        schedule_delay_seconds: float = 1.0,
    ) -> None:
        if not OPENTELEMETRY_AVAILABLE:
            raise ImportError("opentelemetry packages required")
        if not 1 <= max_export_batch_size <= max_queue_size <= 65536:
            raise ValueError("OpenTelemetry batch size must be between 1 and max_queue_size")
        if not 0.05 <= schedule_delay_seconds <= 60:
            raise ValueError("OpenTelemetry schedule_delay_seconds must be between 0.05 and 60")
        self.endpoint = endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        self.headers = headers
        self.service_name = service_name
        self.max_queue_size = max_queue_size
        self.max_export_batch_size = max_export_batch_size
        self.schedule_delay_seconds = schedule_delay_seconds
        self._tracer = None
        self._provider = None
        self._propagator = TraceContextTextMapPropagator()

    @property
    def tracer(self):
        if self._tracer is None:
            provider = TracerProvider(
                resource=Resource.create({"service.name": self.service_name}),
                shutdown_on_exit=False,
            )
            if self.endpoint:
                exporter = OTLPSpanExporter(endpoint=self.endpoint, headers=self.headers, timeout=5)
                # Callback delivery only records the span; one owned thread exports batches.
                provider.add_span_processor(
                    BoundedBatchSpanProcessor(
                        exporter,
                        max_queue_size=self.max_queue_size,
                        max_export_batch_size=self.max_export_batch_size,
                        schedule_delay_seconds=self.schedule_delay_seconds,
                    )
                )
            self._tracer = provider.get_tracer(self.service_name)
            self._provider = provider
        return self._tracer

    def close(self) -> None:
        if self._provider is not None:
            self._provider.shutdown()
            self._provider = None
            self._tracer = None

    def log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        del response_obj
        span = self._start_span(kwargs, start_time)
        try:
            usage = kwargs.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens") or 0)
            output_tokens = int(usage.get("completion_tokens") or 0)
            self._set_common_attributes(span, kwargs)
            span.set_attribute("llm.usage.prompt_tokens", input_tokens)
            span.set_attribute("llm.usage.completion_tokens", output_tokens)
            span.set_attribute("llm.usage.total_tokens", int(usage.get("total_tokens") or 0))
            span.set_attribute("llm.cost", float(kwargs.get("response_cost") or 0.0))
            span.set_attribute("llm.user.id", str(kwargs.get("user") or "unknown"))
            span.set_attribute("llm.team.id", str(kwargs.get("team_id") or "unknown"))
            span.set_attribute("llm.cache.hit", bool(kwargs.get("cache_hit") or False))
            span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
            for name in ("temperature", "max_tokens", "top_p"):
                value = kwargs.get(name)
                if isinstance(value, int | float) and not isinstance(value, bool):
                    span.set_attribute(f"gen_ai.request.{name}", value)
            span.set_status(Status(StatusCode.OK))
        finally:
            span.end(end_time=_nanoseconds(end_time))

    def log_failure_event(
        self,
        kwargs: dict[str, Any],
        exception: Exception,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        span = self._start_span(kwargs, start_time)
        try:
            self._set_common_attributes(span, kwargs)
            error_info = kwargs.get("error_info") or {}
            error_type = error_info.get("error_type") if isinstance(error_info, dict) else None
            # Only the error class name: provider messages can hold prompt or account text.
            span.set_attribute("error.type", str(error_type or "unknown")[:128])
            span.record_exception(exception)
            span.set_status(Status(StatusCode.ERROR, str(exception)))
        finally:
            span.end(end_time=_nanoseconds(end_time))

    def _start_span(self, kwargs: dict[str, Any], start_time: datetime):  # noqa: ANN202
        return self.tracer.start_span(
            name=f"llm.{kwargs.get('call_type', 'completion')}",
            context=self._parent_context(kwargs),
            start_time=_nanoseconds(start_time),
        )

    def _parent_context(self, kwargs: dict[str, Any]):  # noqa: ANN202
        # Gateway payloads always carry a validated trace_context (or None).
        # Only older custom payloads without the field fall back to raw metadata.
        carrier = kwargs.get("trace_context")
        if "trace_context" not in kwargs:
            metadata = kwargs.get("metadata") or {}
            carrier = (
                {"traceparent": metadata["traceparent"]}
                if isinstance(metadata, dict) and "traceparent" in metadata
                else None
            )
        if not carrier:
            return None
        return self._propagator.extract(carrier=carrier)

    @staticmethod
    def _set_common_attributes(span: Any, kwargs: dict[str, Any]) -> None:
        call_type = str(kwargs.get("call_type") or "completion")
        model = str(kwargs.get("model") or "unknown")
        provider = str(kwargs.get("api_provider") or "unknown")
        span.set_attribute("llm.model", model)
        span.set_attribute("llm.provider", provider)
        span.set_attribute("llm.request.type", call_type)
        span.set_attribute("gen_ai.operation.name", _GENAI_OPERATIONS.get(call_type, call_type))
        span.set_attribute("gen_ai.provider.name", provider)
        span.set_attribute("gen_ai.request.model", model)
        if kwargs.get("deployment_model"):
            span.set_attribute("gen_ai.response.model", str(kwargs["deployment_model"]))
        if kwargs.get("request_id"):
            span.set_attribute("deltallm.request_id", str(kwargs["request_id"]))


def _nanoseconds(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)
