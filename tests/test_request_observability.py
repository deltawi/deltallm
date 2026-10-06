from __future__ import annotations

import asyncio
from collections.abc import Callable
import gc
import threading
from typing import cast

import pytest
from starlette.types import Message, Receive, Scope, Send

from prometheus_client import CollectorRegistry

from src.bootstrap.metrics import (
    freeze_startup_heap,
    MetricsSnapshotGenerationTimedOut,
    MetricsSnapshotTooLarge,
    PrometheusSnapshotService,
    RuntimeMetricSampler,
)
from src.metrics.prometheus import get_prometheus_registry
from src.metrics.request_phases import measure_request_phase, observe_request_phase, request_route
from src.middleware.request_timing import RequestTimingMiddleware


def value(name: str, **labels: str) -> float:
    return get_prometheus_registry().get_sample_value(name, labels) or 0.0


def phase(phase_name: str, outcome: str = "success") -> float:
    return value(
        "deltallm_request_phase_latency_seconds_count",
        route="chat_completions",
        phase=phase_name,
        outcome=outcome,
        response_kind="stream",
    )


@pytest.mark.asyncio
async def test_http_lifetime_excludes_after_response_cleanup_without_losing_cancellation() -> None:
    first_body = asyncio.Event()
    finish_body = asyncio.Event()
    body_finished = asyncio.Event()
    cleanup = asyncio.Event()
    received = {"type": "http.request", "body": b"request", "more_body": False}
    sent: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        assert await receive() is received
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send({"type": "http.response.body", "body": b"data: OK\n\n", "more_body": True})
        first_body.set()
        await finish_body.wait()
        await send({"type": "http.response.body", "body": b"", "more_body": False})
        body_finished.set()
        await cleanup.wait()

    async def receive() -> Message:
        return received

    async def send(message: Message) -> None:
        sent.append(message)

    before_active = value("deltallm_http_requests_in_flight", route="chat_completions")
    before_body = phase("response_first_body")
    before_total = phase("response_total")
    before_application = phase("application_total", "cancelled")
    before_received = value("deltallm_http_request_body_bytes_total", route="chat_completions")
    task = asyncio.create_task(
        RequestTimingMiddleware(app)(
            {"type": "http", "path": "/v1/chat/completions"}, receive, send
        )
    )
    try:
        await asyncio.wait_for(first_body.wait(), 1)
        assert (
            value("deltallm_http_requests_in_flight", route="chat_completions") == before_active + 1
        )
        assert phase("response_first_body") == before_body + 1
        assert phase("response_total") == before_total
        assert (
            value("deltallm_http_request_body_bytes_total", route="chat_completions")
            == before_received + 7
        )
        finish_body.set()
        await asyncio.wait_for(body_finished.wait(), 1)
        assert value("deltallm_http_requests_in_flight", route="chat_completions") == before_active
        assert phase("response_total") == before_total + 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert phase("application_total", "cancelled") == before_application + 1
    assert phase("response_total") == before_total + 1
    assert sent[-1]["body"] == b""


@pytest.mark.asyncio
async def test_failed_send_releases_live_request_and_does_not_report_sent_bytes() -> None:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"private", "more_body": False})

    async def receive() -> Message:
        raise AssertionError("middleware must not eagerly consume the body")

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            raise OSError("connection closed")

    before = value("deltallm_http_response_body_bytes_total", route="other")
    with pytest.raises(OSError):
        await RequestTimingMiddleware(app)({"type": "http", "path": "/unknown"}, receive, send)
    assert value("deltallm_http_requests_in_flight", route="other") == 0
    assert value("deltallm_http_response_body_bytes_total", route="other") == before


def test_untrusted_phase_values_collapse_to_fixed_labels() -> None:
    before = value(
        "deltallm_request_phase_latency_seconds_count",
        route="other",
        phase="other",
        outcome="other",
        response_kind="unknown",
    )
    for index in range(100):
        untrusted = f"private-{index}"
        observe_request_phase(
            route=untrusted,
            phase=untrusted,
            outcome=untrusted,
            response_kind=untrusted,
            latency_seconds=0.01,
        )
    assert (
        value(
            "deltallm_request_phase_latency_seconds_count",
            route="other",
            phase="other",
            outcome="other",
            response_kind="unknown",
        )
        == before + 100
    )
    for metric in get_prometheus_registry().collect():
        if metric.name == "deltallm_request_phase_latency_seconds":
            assert all("private-" not in str(sample.labels) for sample in metric.samples)


def test_request_phase_in_flight_is_bounded_and_released_on_error() -> None:
    labels = {"route": "chat_completions", "phase": "upstream_http"}
    before = value("deltallm_request_phase_in_flight", **labels)

    with pytest.raises(RuntimeError, match="synthetic"):
        with measure_request_phase(
            route="chat_completions",
            phase="upstream_http",
            response_kind="nonstream",
        ):
            assert value("deltallm_request_phase_in_flight", **labels) == before + 1
            raise RuntimeError("synthetic")

    assert value("deltallm_request_phase_in_flight", **labels) == before


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/v1/chat/completions", "chat_completions"),
        ("/chat/completions", "chat_completions"),
        ("/audio/transcriptions", "audio"),
        ("/v1/images/generations", "images"),
        ("/v1/messages", "messages"),
        ("/v1/completions", "completions"),
        ("/unknown/private-user", "other"),
    ],
)
def test_route_aliases_remain_bounded(path: str, expected: str) -> None:
    assert request_route(path) == expected


class Timer:
    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.timers: list[Timer] = []

    def time(self) -> float:
        return self.now

    def call_later(self, delay: float, callback: Callable[[], None]) -> Timer:
        assert delay == 1
        timer = Timer(callback)
        self.timers.append(timer)
        return timer


def test_sampler_measures_delay_and_cannot_rearm_after_shutdown() -> None:
    clock = Clock()
    sampler = RuntimeMetricSampler(cast(asyncio.AbstractEventLoop, clock))
    before = value("deltallm_event_loop_samplers")
    sampler.start()
    sampler.start()
    assert len(clock.timers) == 1
    assert value("deltallm_event_loop_samplers") == before + 1
    before_gc = value("deltallm_python_gc_pause_seconds_count", generation="2")
    sampler._observe_gc("start", {"generation": 2})
    sampler._observe_gc("stop", {"generation": 2})
    assert value("deltallm_python_gc_pause_seconds_count", generation="2") == before_gc + 1
    clock.now = 101.25
    clock.timers[0].callback()
    assert value("deltallm_event_loop_last_lag_seconds") == 0.25
    assert len(clock.timers) == 2
    sampler.close()
    sampler.close()
    assert clock.timers[-1].cancelled
    clock.timers[-1].callback()
    assert len(clock.timers) == 2
    assert value("deltallm_event_loop_samplers") == before


def test_startup_heap_is_collected_before_it_is_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        "src.bootstrap.metrics.gc.collect", lambda generation: calls.append(generation)
    )
    monkeypatch.setattr("src.bootstrap.metrics.gc.freeze", lambda: calls.append("freeze"))
    monkeypatch.setattr("src.bootstrap.metrics.gc.get_freeze_count", lambda: 321)

    assert freeze_startup_heap() == 321
    assert calls == [2, "freeze"]


@pytest.mark.asyncio
async def test_prometheus_snapshot_encoding_runs_outside_event_loop() -> None:
    event_loop_thread = threading.get_ident()
    encoder_threads: list[int] = []
    second_started = threading.Event()
    release_second = threading.Event()

    def encode(registry: CollectorRegistry) -> bytes:
        del registry
        encoder_threads.append(threading.get_ident())
        if len(encoder_threads) == 1:
            return b"initial"
        second_started.set()
        assert release_second.wait(timeout=1)
        return b"refreshed"

    service = PrometheusSnapshotService(
        registry=CollectorRegistry(),
        encoder=encode,
        interval_seconds=3600,
    )
    await service.start(periodic=False)
    refresh = asyncio.create_task(service.refresh())
    try:
        for _ in range(100):
            if second_started.is_set():
                break
            await asyncio.sleep(0)
        assert second_started.is_set()
        assert service.snapshot.content == b"initial"
        assert encoder_threads == [encoder_threads[0], encoder_threads[0]]
        assert encoder_threads[0] != event_loop_thread
        event_loop_progressed = asyncio.Event()
        asyncio.get_running_loop().call_soon(event_loop_progressed.set)
        await asyncio.wait_for(event_loop_progressed.wait(), timeout=0.1)
        release_second.set()
        assert (await refresh).content == b"refreshed"
    finally:
        release_second.set()
        await asyncio.gather(refresh, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_prometheus_snapshot_failure_preserves_last_completed_content() -> None:
    calls = 0

    def encode(registry: CollectorRegistry) -> bytes:
        nonlocal calls
        del registry
        calls += 1
        if calls == 1:
            return b"last-good"
        raise RuntimeError("broken collector")

    service = PrometheusSnapshotService(
        registry=CollectorRegistry(),
        encoder=encode,
        interval_seconds=3600,
    )
    await service.start(periodic=False)
    try:
        with pytest.raises(RuntimeError, match="broken collector"):
            await service.refresh()
        assert service.snapshot.content == b"last-good"
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("late", [False, True])
async def test_failed_snapshot_wrapper_has_no_unobserved_exception(late: bool) -> None:
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    observed: list[str] = []
    release = threading.Event()
    finished = asyncio.Event()
    calls = 0

    def encode(registry: CollectorRegistry) -> bytes:
        nonlocal calls
        del registry
        calls += 1
        if calls == 1:
            return b"last-good"
        try:
            if late:
                assert release.wait(timeout=1)
            raise RuntimeError("Synthetic encoder failure")
        finally:
            loop.call_soon_threadsafe(finished.set)

    service = PrometheusSnapshotService(
        registry=CollectorRegistry(),
        encoder=encode,
        execution_timeout_seconds=0.01 if late else 1,
    )
    await service.start(periodic=False)
    loop.set_exception_handler(lambda _loop, context: observed.append(str(context["message"])))
    try:
        expected = MetricsSnapshotGenerationTimedOut if late else RuntimeError
        with pytest.raises(expected):
            await service.refresh()
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=0.5)
        await service.close()
        gc.collect()
        await asyncio.sleep(0)
        assert service.snapshot.content == b"last-good"
        assert observed == []
    finally:
        release.set()
        await service.close()
        loop.set_exception_handler(previous_handler)


@pytest.mark.asyncio
async def test_prometheus_snapshot_rejects_oversized_content_and_preserves_last_good() -> None:
    payloads = iter((b"ok", b"too-large"))
    service = PrometheusSnapshotService(
        registry=CollectorRegistry(),
        encoder=lambda registry: next(payloads),
        interval_seconds=3600,
        max_snapshot_bytes=2,
    )
    await service.start(periodic=False)
    try:
        with pytest.raises(MetricsSnapshotTooLarge, match="retained byte budget"):
            await service.refresh()
        assert service.snapshot.content == b"ok"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_prometheus_snapshot_timeout_cannot_queue_more_encoder_work() -> None:
    calls = 0
    second_started = threading.Event()
    second_finished = threading.Event()
    release_second = threading.Event()

    def encode(registry: CollectorRegistry) -> bytes:
        nonlocal calls
        del registry
        calls += 1
        if calls == 1:
            return b"initial"
        if calls == 2:
            second_started.set()
            release_second.wait(timeout=1)
            second_finished.set()
            return b"discarded-late-result"
        return b"fresh"

    service = PrometheusSnapshotService(
        registry=CollectorRegistry(),
        encoder=encode,
        interval_seconds=3600,
        execution_timeout_seconds=0.01,
    )
    await service.start(periodic=False)
    try:
        with pytest.raises(MetricsSnapshotGenerationTimedOut, match="deadline exceeded"):
            await service.refresh()
        assert second_started.is_set()
        assert service.snapshot.content == b"initial"

        # A timed-out thread retains the only encoder slot. Further refreshes
        # serve the last good snapshot instead of queuing executor work.
        assert (await service.refresh()).content == b"initial"
        assert calls == 2

        release_second.set()
        for _ in range(100):
            if second_finished.is_set() and service._generation.done():
                break
            await asyncio.sleep(0)
        assert second_finished.is_set()
        assert service._generation.done()
        assert (await service.refresh()).content == b"fresh"
        assert calls == 3
    finally:
        release_second.set()
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("termination", ["receive", "send", "cancelled_send"])
async def test_disconnect_finishes_http_before_application_cleanup(
    termination: str,
) -> None:
    disconnected = asyncio.Event()
    cleanup = asyncio.Event()
    before_active = value("deltallm_http_requests_in_flight", route="chat_completions")
    outcome = "cancelled" if termination == "cancelled_send" else "disconnected"
    before_total = phase("response_total", outcome)
    before_bytes = value("deltallm_http_response_body_bytes_total", route="chat_completions")

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        if termination != "receive":
            error = asyncio.CancelledError if termination == "cancelled_send" else OSError
            with pytest.raises(error):
                await send({"type": "http.response.body", "body": b"unsent", "more_body": True})
        else:
            assert (await receive())["type"] == "http.disconnect"
            await receive()  # Duplicate notifications must not double-release.
            await send({"type": "http.response.body", "body": b"discarded", "more_body": False})
        disconnected.set()
        await cleanup.wait()

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        if termination != "receive" and message["type"] == "http.response.body":
            error = asyncio.CancelledError if termination == "cancelled_send" else OSError
            raise error("closed")

    task = asyncio.create_task(
        RequestTimingMiddleware(app)(
            {"type": "http", "path": "/v1/chat/completions"}, receive, send
        )
    )
    try:
        await asyncio.wait_for(disconnected.wait(), 1)
        assert not task.done()
        assert value("deltallm_http_requests_in_flight", route="chat_completions") == before_active
        assert phase("response_total", outcome) == before_total + 1
        assert (
            value("deltallm_http_response_body_bytes_total", route="chat_completions")
            == before_bytes
        )
        cleanup.set()
        await task
        assert phase("response_total", outcome) == before_total + 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
