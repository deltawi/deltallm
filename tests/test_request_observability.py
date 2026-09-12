from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast

import pytest
from starlette.types import Message, Receive, Scope, Send

from src.bootstrap.metrics import RuntimeMetricSampler
from src.metrics.prometheus import get_prometheus_registry
from src.metrics.request_phases import observe_request_phase, request_route
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
