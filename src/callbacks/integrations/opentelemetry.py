from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from src.callbacks.base import CustomLogger

try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.trace import Status, StatusCode
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False


class OpenTelemetryCallback(CustomLogger):
    def __init__(
        self,
        endpoint: str | None = None,
        headers: dict[str, str] | None = None,
        service_name: str = "deltallm",
    ) -> None:
        if not OPENTELEMETRY_AVAILABLE:
            raise ImportError("opentelemetry packages required")
        self.endpoint = endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        self.headers = headers
        self.service_name = service_name
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
                provider.add_span_processor(SimpleSpanProcessor(exporter))
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
        metadata = kwargs.get("metadata") or {}
        context = None
        if isinstance(metadata, dict) and "traceparent" in metadata:
            context = self._propagator.extract(carrier={"traceparent": metadata["traceparent"]})

        span = self.tracer.start_span(
            name=f"llm.{kwargs.get('call_type', 'completion')}",
            context=context,
            start_time=int(start_time.timestamp() * 1_000_000_000),
        )
        try:
            usage = kwargs.get("usage") or {}
            span.set_attribute("llm.model", str(kwargs.get("model") or "unknown"))
            span.set_attribute("llm.provider", str(kwargs.get("api_provider") or "unknown"))
            span.set_attribute("llm.request.type", str(kwargs.get("call_type") or "completion"))
            span.set_attribute("llm.usage.prompt_tokens", int(usage.get("prompt_tokens") or 0))
            span.set_attribute(
                "llm.usage.completion_tokens", int(usage.get("completion_tokens") or 0)
            )
            span.set_attribute("llm.usage.total_tokens", int(usage.get("total_tokens") or 0))
            span.set_attribute("llm.cost", float(kwargs.get("response_cost") or 0.0))
            span.set_attribute("llm.user.id", str(kwargs.get("user") or "unknown"))
            span.set_attribute("llm.team.id", str(kwargs.get("team_id") or "unknown"))
            span.set_attribute("llm.cache.hit", bool(kwargs.get("cache_hit") or False))
            span.set_status(Status(StatusCode.OK))
        finally:
            span.end(end_time=int(end_time.timestamp() * 1_000_000_000))

    def log_failure_event(
        self,
        kwargs: dict[str, Any],
        exception: Exception,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        metadata = kwargs.get("metadata") or {}
        context = None
        if isinstance(metadata, dict) and "traceparent" in metadata:
            context = self._propagator.extract(carrier={"traceparent": metadata["traceparent"]})

        span = self.tracer.start_span(
            name=f"llm.{kwargs.get('call_type', 'completion')}",
            context=context,
            start_time=int(start_time.timestamp() * 1_000_000_000),
        )
        try:
            span.set_attribute("llm.model", str(kwargs.get("model") or "unknown"))
            span.set_attribute("llm.provider", str(kwargs.get("api_provider") or "unknown"))
            span.record_exception(exception)
            span.set_status(Status(StatusCode.ERROR, str(exception)))
        finally:
            span.end(end_time=int(end_time.timestamp() * 1_000_000_000))
