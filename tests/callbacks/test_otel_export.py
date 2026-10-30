"""Bounded OpenTelemetry export: counted drops, batched export, bounded shutdown."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from src.metrics.prometheus import PROMETHEUS_REGISTRY as REGISTRY

from src.callbacks.integrations import opentelemetry as otel
from src.callbacks.integrations.otel_export import BoundedBatchSpanProcessor, SpanExportResult

pytestmark = pytest.mark.hermetic


def _count(outcome: str) -> float:
    return REGISTRY.get_sample_value("deltallm_otel_spans_total", {"outcome": outcome}) or 0.0


def _span(sampled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(context=SimpleNamespace(trace_flags=SimpleNamespace(sampled=sampled)))


class _Exporter:
    def __init__(self, result=SpanExportResult.SUCCESS, gate: threading.Event | None = None):
        self.result = result
        self.gate = gate
        self.batches: list[int] = []
        self.shutdowns = 0

    def export(self, batch):
        if self.gate is not None:
            self.gate.wait(2)
        self.batches.append(len(batch))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def shutdown(self):
        self.shutdowns += 1


def test_full_queue_drops_and_counts_spans():
    gate = threading.Event()
    exporter = _Exporter(gate=gate)
    processor = BoundedBatchSpanProcessor(
        exporter, max_queue_size=2, max_export_batch_size=2, schedule_delay_seconds=60
    )
    dropped = _count("dropped")
    exported = _count("exported")
    try:
        for _ in range(5):
            processor.on_end(_span())
        processor.on_end(_span(sampled=False))
    finally:
        gate.set()
        processor.shutdown()

    assert _count("dropped") - dropped >= 1
    assert _count("exported") - exported == sum(exporter.batches)
    assert sum(exporter.batches) + (_count("dropped") - dropped) == 5
    assert exporter.shutdowns == 1


def test_export_failures_are_counted_and_batches_are_bounded():
    exporter = _Exporter(result=RuntimeError("collector down"))
    processor = BoundedBatchSpanProcessor(
        exporter, max_queue_size=10, max_export_batch_size=3, schedule_delay_seconds=60
    )
    failed = _count("export_failed")
    for _ in range(7):
        processor.on_end(_span())
    processor.force_flush()
    processor.shutdown()
    processor.on_end(_span())

    assert max(exporter.batches) <= 3
    assert sum(exporter.batches) == 7
    assert _count("export_failed") - failed == 7


def test_invalid_batch_settings_are_rejected():
    with pytest.raises(ValueError):
        BoundedBatchSpanProcessor(_Exporter(), max_queue_size=2, max_export_batch_size=3)


@pytest.mark.skipif(not otel.OPENTELEMETRY_AVAILABLE, reason="OpenTelemetry SDK not installed")
def test_span_uses_header_parent_and_genai_attributes():
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    handler = otel.OpenTelemetryCallback(endpoint=None)
    memory = InMemorySpanExporter()
    handler.tracer  # noqa: B018 - builds the provider
    handler._provider.add_span_processor(BoundedBatchSpanProcessor(memory))
    start = datetime.now(tz=UTC)
    trace_id = "0af7651916cd43dd8448eb211c80319c"
    kwargs = {
        "call_type": "completion",
        "model": "gpt-4o-mini",
        "deployment_model": "openai/gpt-4o-mini",
        "api_provider": "openai",
        "request_id": "req-9",
        "usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
        "temperature": 0.2,
        "trace_context": {"traceparent": f"00-{trace_id}-b7ad6b7169203331-01"},
        "error_info": {"error_type": "RateLimitError", "message": "private provider text"},
    }
    try:
        handler.log_success_event(kwargs, None, start, start + timedelta(seconds=1))
        handler.log_failure_event(
            kwargs, RuntimeError("Gateway request failed"), start, start + timedelta(seconds=1)
        )
        handler._provider.force_flush()
        success, failure = memory.get_finished_spans()
    finally:
        handler.close()

    assert format(success.context.trace_id, "032x") == trace_id
    assert success.attributes["gen_ai.operation.name"] == "chat"
    assert success.attributes["gen_ai.provider.name"] == "openai"
    assert success.attributes["gen_ai.request.model"] == "gpt-4o-mini"
    assert success.attributes["gen_ai.response.model"] == "openai/gpt-4o-mini"
    assert success.attributes["gen_ai.usage.input_tokens"] == 4
    assert success.attributes["gen_ai.usage.output_tokens"] == 6
    assert success.attributes["gen_ai.request.temperature"] == 0.2
    assert success.attributes["deltallm.request_id"] == "req-9"
    assert failure.attributes["error.type"] == "RateLimitError"
    assert "private provider text" not in str(dict(failure.attributes))


def test_optional_work_metrics_are_served_by_the_gateway_registry():
    from prometheus_client import generate_latest

    from src.metrics.request_work import callback_outcomes

    callback_outcomes.labels("opentelemetry", "completed").inc(0)
    text = generate_latest(REGISTRY).decode()
    assert "deltallm_callback_outcomes_total" in text
    assert "deltallm_otel_spans_total" in text
