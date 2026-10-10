#!/usr/bin/env python3
"""Constant-arrival and simultaneous-wave load measurement for DeltaLLM.

The tool deliberately separates the arrival window from drain time. Secrets are
accepted only through CLI/environment/file inputs and are never written to the
result artifacts.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from functools import partial
import gzip
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
from time import perf_counter, time
from uuid import uuid4

import httpx

MAX_RESPONSE_BYTES = 65_536
MAX_SAMPLES = 1_000_000
MAX_RATE = 1000
MAX_IN_FLIGHT = 10_000
MAX_DURATION_SECONDS = 1_800
DEFAULT_DRAIN_SECONDS = 60
SAFE_ERROR_CODES = frozenset(
    {
        "gateway_draining",
        "request_deadline_exceeded",
        "gateway_work_unavailable",
        "edge_unavailable",
        "gateway_ingress_full",
        "gateway_ingress_buffer_full",
        "gateway_request_body_too_large",
        "gateway_request_body_timeout",
        "invalid_content_length",
        "auth_fallback_unavailable",
        "database_unavailable",
        "audit_persistence_unavailable",
        "gateway_preflight_global_parallel_exceeded",
        "gateway_preflight_org_parallel_exceeded",
        "prompt_resolution_timeout",
        "spend_ingestion_unavailable",
        "spend_persistence_unavailable",
        "provider_unavailable",
        "no_healthy_deployments",
        "rate_limit_exceeded",
    }
)


@dataclass(frozen=True, slots=True)
class RequestResult:
    status_code: int | None
    error: str | None = None
    ttft_seconds: float | None = None
    bytes_received: int = 0
    client_connection_wait_seconds: float | None = None
    client_connection_setup_seconds: float | None = None
    client_request_write_seconds: float | None = None
    client_response_header_seconds: float | None = None
    client_response_read_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class RequestSample:
    index: int
    request_id: str
    scheduled_offset_seconds: float
    start_offset_seconds: float
    completion_offset_seconds: float
    scheduling_lag_seconds: float
    latency_seconds: float
    completed_in_arrival_window: bool
    status_code: int | None
    error: str | None
    ttft_seconds: float | None
    bytes_received: int
    client_connection_wait_seconds: float | None = None
    client_connection_setup_seconds: float | None = None
    client_request_write_seconds: float | None = None
    client_response_header_seconds: float | None = None
    client_response_read_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    started_at: str
    target_count: int
    scheduled_count: int
    generator_dropped_count: int
    arrival_window_seconds: float
    drain_window_seconds: float
    max_in_flight_observed: int
    samples: tuple[RequestSample, ...]
    max_tracked_tasks_observed: int = 0
    generator_worker_count: int = 1
    generator_start_skew_seconds: float = 0.0


RequestFunction = Callable[[int, str], Awaitable[RequestResult]]


@dataclass(slots=True)
class ClientPhaseTrace:
    """Collect bounded httpcore phase timestamps for one request."""

    started: float
    events: dict[str, float]

    @classmethod
    def start(cls) -> ClientPhaseTrace:
        return cls(started=perf_counter(), events={})

    async def __call__(self, name: str, _info: dict[str, object]) -> None:
        if name in {
            "connection.connect_tcp.started",
            "connection.connect_tcp.complete",
            "connection.start_tls.started",
            "connection.start_tls.complete",
            "http11.send_request_headers.started",
            "http11.send_request_body.complete",
            "http11.receive_response_headers.complete",
            "http11.receive_response_body.complete",
            "http2.send_request_headers.started",
            "http2.send_request_body.complete",
            "http2.receive_response_headers.complete",
            "http2.receive_response_body.complete",
        }:
            self.events.setdefault(name, perf_counter())

    def result_fields(self, *, completed: float) -> dict[str, float | None]:
        send_started = self._event("send_request_headers.started")
        send_completed = self._event("send_request_body.complete")
        headers_completed = self._event("receive_response_headers.complete")
        body_completed = self._event("receive_response_body.complete") or completed
        connect_started = self.events.get("connection.connect_tcp.started")
        setup_finished = send_started
        return {
            "client_connection_wait_seconds": _elapsed(
                self.started,
                connect_started or send_started,
            ),
            "client_connection_setup_seconds": _elapsed(connect_started, setup_finished),
            "client_request_write_seconds": _elapsed(send_started, send_completed),
            "client_response_header_seconds": _elapsed(self.started, headers_completed),
            "client_response_read_seconds": _elapsed(headers_completed, body_completed),
        }

    def _event(self, suffix: str) -> float | None:
        return next(
            (timestamp for name, timestamp in self.events.items() if name.endswith(suffix)),
            None,
        )


def _elapsed(started: float | None, completed: float | None) -> float | None:
    if started is None or completed is None:
        return None
    return max(0.0, completed - started)


def classify_response(
    status_code: int,
    body: bytes,
    *,
    expect_fixed_one_token: bool,
) -> str | None:
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return "invalid_response"
    if status_code >= 400:
        if isinstance(payload, dict):
            error = payload.get("error", payload.get("detail"))
            if (
                isinstance(error, dict)
                and isinstance(error.get("code"), str)
                and error["code"] in SAFE_ERROR_CODES
            ):
                return str(error["code"])
        return "unclassified_http_error"
    if not expect_fixed_one_token:
        return None
    if not isinstance(payload, dict):
        return "invalid_response"
    usage = payload.get("usage")
    choices = payload.get("choices")
    if (
        not isinstance(usage, dict)
        or usage.get("completion_tokens") != 1
        or not isinstance(choices, list)
        or len(choices) != 1
        or not isinstance(choices[0], dict)
        or not isinstance(choices[0].get("message"), dict)
        or choices[0]["message"].get("role") != "assistant"
        or choices[0]["message"].get("content") != "OK"
    ):
        return "invalid_response"
    return None


async def run_constant_arrival(
    *,
    rate: float,
    duration_seconds: float,
    max_in_flight: int,
    request: RequestFunction,
    drain_timeout_seconds: float | None = None,
    target_count: int | None = None,
    schedule_offset_seconds: float = 0.0,
) -> RunResult:
    if (
        not math.isfinite(rate)
        or not 0 < rate <= MAX_RATE
        or not math.isfinite(duration_seconds)
        or not 0 < duration_seconds <= MAX_DURATION_SECONDS
        or isinstance(max_in_flight, bool)
        or not isinstance(max_in_flight, int)
        or not 1 <= max_in_flight <= MAX_IN_FLIGHT
        or (
            drain_timeout_seconds is not None
            and (not math.isfinite(drain_timeout_seconds) or not 0 < drain_timeout_seconds <= 120)
        )
        or (
            target_count is not None
            and (
                isinstance(target_count, bool)
                or not isinstance(target_count, int)
                or not 1 <= target_count <= MAX_SAMPLES
            )
        )
        or not 0 <= schedule_offset_seconds < duration_seconds
    ):
        raise ValueError("arrival rate, duration, target, offset, and concurrency are invalid")

    run_id = uuid4().hex
    started_at = datetime.now(tz=UTC).isoformat()
    target_count = target_count or max(1, int(math.floor(rate * duration_seconds)))
    if target_count > MAX_SAMPLES:
        raise ValueError("load generator sample budget exceeded")
    drain_timeout_seconds = drain_timeout_seconds or DEFAULT_DRAIN_SECONDS
    interval = 1.0 / rate
    final_scheduled_offset = schedule_offset_seconds + ((target_count - 1) * interval)
    if final_scheduled_offset >= duration_seconds:
        raise ValueError("target count and schedule offset exceed the arrival window")
    started = perf_counter()
    arrival_deadline = started + duration_seconds
    active = 0
    max_active = 0
    scheduled_count = 0
    dropped = 0
    tasks: set[asyncio.Task[RequestSample]] = set()
    max_tracked_tasks = 0
    drain_expired: set[asyncio.Task[RequestSample]] = set()
    samples: list[RequestSample] = []

    async def execute(index: int, request_id: str, scheduled_at: float) -> RequestSample:
        nonlocal active
        actual_start = perf_counter()
        try:
            try:
                result = await request(index, request_id)
            except asyncio.CancelledError:
                if asyncio.current_task() not in drain_expired:
                    raise
                result = RequestResult(status_code=None, error="generator_drain_timeout")
            except Exception as exc:  # the harness must retain every client-side failure
                result = RequestResult(status_code=None, error=exc.__class__.__name__)
            completed = perf_counter()
            sample = RequestSample(
                index=index,
                request_id=request_id,
                scheduled_offset_seconds=scheduled_at - started,
                start_offset_seconds=actual_start - started,
                completion_offset_seconds=completed - started,
                scheduling_lag_seconds=max(0.0, actual_start - scheduled_at),
                latency_seconds=max(0.0, completed - actual_start),
                completed_in_arrival_window=completed <= arrival_deadline,
                status_code=result.status_code,
                error=result.error,
                ttft_seconds=result.ttft_seconds,
                bytes_received=result.bytes_received,
                client_connection_wait_seconds=result.client_connection_wait_seconds,
                client_connection_setup_seconds=result.client_connection_setup_seconds,
                client_request_write_seconds=result.client_request_write_seconds,
                client_response_header_seconds=result.client_response_header_seconds,
                client_response_read_seconds=result.client_response_read_seconds,
            )
            samples.append(sample)
            return sample
        finally:
            active -= 1

    try:
        for index in range(target_count):
            scheduled_at = started + schedule_offset_seconds + (index * interval)
            delay = scheduled_at - perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            if active >= max_in_flight or len(tasks) >= max_in_flight:
                dropped += 1
                continue
            active += 1
            max_active = max(max_active, active)
            request_id = f"load-{run_id}-{index}"
            task = asyncio.create_task(execute(index, request_id, scheduled_at))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            scheduled_count += 1
            max_tracked_tasks = max(max_tracked_tasks, len(tasks))

        remaining = arrival_deadline - perf_counter()
        if remaining > 0:
            await asyncio.sleep(remaining)
        drain_started = perf_counter()
        active_tasks = tuple(tasks)
        if not active_tasks:
            pass
        else:
            _done, pending = await asyncio.wait(active_tasks, timeout=drain_timeout_seconds)
            drain_expired.update(pending)
            for task in pending:
                task.cancel()
            if pending:
                _cancelled, leaked = await asyncio.wait(pending, timeout=5)
                if leaked:
                    raise RuntimeError("load generator request cancellation did not drain")
    except BaseException:
        pending_tasks = tuple(tasks)
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.wait(pending_tasks, timeout=5)
        raise
    finished = perf_counter()
    return RunResult(
        run_id=run_id,
        started_at=started_at,
        target_count=target_count,
        scheduled_count=scheduled_count,
        generator_dropped_count=dropped,
        arrival_window_seconds=duration_seconds,
        drain_window_seconds=max(0.0, finished - drain_started),
        max_in_flight_observed=max_active,
        max_tracked_tasks_observed=max_tracked_tasks,
        samples=tuple(sorted(samples, key=lambda item: item.index)),
    )


async def run_simultaneous_wave(
    *,
    concurrency: int,
    request: RequestFunction,
    drain_timeout_seconds: float = DEFAULT_DRAIN_SECONDS,
) -> RunResult:
    if (
        isinstance(concurrency, bool)
        or not isinstance(concurrency, int)
        or not 1 <= concurrency <= MAX_IN_FLIGHT
        or not math.isfinite(drain_timeout_seconds)
        or not 0 < drain_timeout_seconds <= 120
    ):
        raise ValueError("wave concurrency and drain deadline are invalid")
    run_id = uuid4().hex
    started_at = datetime.now(tz=UTC).isoformat()
    gate = asyncio.Event()
    all_ready = asyncio.Event()
    ready = 0
    wave_started = perf_counter()

    async def execute(index: int) -> RequestSample:
        nonlocal ready
        ready += 1
        if ready == concurrency:
            all_ready.set()
        await gate.wait()
        actual_start = perf_counter()
        request_id = f"load-{run_id}-{index}"
        try:
            result = await request(index, request_id)
        except Exception as exc:
            result = RequestResult(status_code=None, error=exc.__class__.__name__)
        completed = perf_counter()
        return RequestSample(
            index=index,
            request_id=request_id,
            scheduled_offset_seconds=0.0,
            start_offset_seconds=actual_start - wave_started,
            completion_offset_seconds=completed - wave_started,
            scheduling_lag_seconds=max(0.0, actual_start - wave_started),
            latency_seconds=max(0.0, completed - actual_start),
            completed_in_arrival_window=False,
            status_code=result.status_code,
            error=result.error,
            ttft_seconds=result.ttft_seconds,
            bytes_received=result.bytes_received,
            client_connection_wait_seconds=result.client_connection_wait_seconds,
            client_connection_setup_seconds=result.client_connection_setup_seconds,
            client_request_write_seconds=result.client_request_write_seconds,
            client_response_header_seconds=result.client_response_header_seconds,
            client_response_read_seconds=result.client_response_read_seconds,
        )

    tasks = [asyncio.create_task(execute(index)) for index in range(concurrency)]
    try:
        await all_ready.wait()
        wave_started = perf_counter()
        gate.set()
        async with asyncio.timeout(drain_timeout_seconds):
            samples = tuple(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.wait(tasks, timeout=5)
        raise
    finished = perf_counter()
    return RunResult(
        run_id=run_id,
        started_at=started_at,
        target_count=concurrency,
        scheduled_count=concurrency,
        generator_dropped_count=0,
        arrival_window_seconds=max(1e-9, finished - wave_started),
        drain_window_seconds=0.0,
        max_in_flight_observed=concurrency,
        max_tracked_tasks_observed=concurrency,
        samples=tuple(sorted(samples, key=lambda item: item.index)),
    )


def merge_run_results(results: Sequence[RunResult]) -> RunResult:
    """Merge synchronized generator workers into one bounded evidence stream."""

    if not 1 <= len(results) <= 16:
        raise ValueError("between one and sixteen generator worker results are required")
    if sum(result.target_count for result in results) > MAX_SAMPLES:
        raise ValueError("merged load generator sample budget exceeded")
    starts = [datetime.fromisoformat(result.started_at) for result in results]
    if any(start.tzinfo is None for start in starts):
        raise ValueError("generator worker start timestamps must include a timezone")
    base = min(starts)
    offsets = [(start - base).total_seconds() for start in starts]
    skew = max(offsets)
    arrival_window = max(result.arrival_window_seconds for result in results)
    if skew >= arrival_window:
        raise ValueError("generator workers did not share an arrival window")

    merged_samples: list[RequestSample] = []
    worker_count = len(results)
    for worker, (result, offset) in enumerate(zip(results, offsets, strict=True)):
        for sample in result.samples:
            completion_offset = sample.completion_offset_seconds + offset
            merged_samples.append(
                replace(
                    sample,
                    index=(sample.index * worker_count) + worker,
                    scheduled_offset_seconds=sample.scheduled_offset_seconds + offset,
                    start_offset_seconds=sample.start_offset_seconds + offset,
                    completion_offset_seconds=completion_offset,
                    completed_in_arrival_window=completion_offset <= arrival_window,
                )
            )
    merged_samples.sort(key=lambda sample: sample.index)
    active = 0
    max_active = 0
    events = [
        event
        for sample in merged_samples
        for event in (
            (sample.start_offset_seconds, 1),
            (sample.completion_offset_seconds, -1),
        )
    ]
    for _offset, change in sorted(events, key=lambda event: (event[0], event[1])):
        active += change
        max_active = max(max_active, active)

    drain_finished = max(
        offset + result.arrival_window_seconds + result.drain_window_seconds
        for result, offset in zip(results, offsets, strict=True)
    )
    return RunResult(
        run_id=uuid4().hex,
        started_at=base.isoformat(),
        target_count=sum(result.target_count for result in results),
        scheduled_count=sum(result.scheduled_count for result in results),
        generator_dropped_count=sum(result.generator_dropped_count for result in results),
        arrival_window_seconds=arrival_window,
        drain_window_seconds=max(0.0, drain_finished - arrival_window),
        max_in_flight_observed=max_active,
        max_tracked_tasks_observed=sum(result.max_tracked_tasks_observed for result in results),
        samples=tuple(merged_samples),
        generator_worker_count=worker_count,
        generator_start_skew_seconds=skew,
    )


def summarize(result: RunResult, *, target_rate: float | None) -> dict[str, object]:
    samples = list(result.samples)
    successes = [
        sample
        for sample in samples
        if sample.status_code is not None
        and 200 <= sample.status_code < 300
        and sample.error is None
    ]
    arrival_completions = [sample for sample in samples if sample.completed_in_arrival_window]
    latencies = [sample.latency_seconds for sample in samples]
    schedule_lags = [sample.scheduling_lag_seconds for sample in samples]
    ttfts = [sample.ttft_seconds for sample in samples if sample.ttft_seconds is not None]
    client_phases = {
        name: [value for sample in samples if (value := getattr(sample, name)) is not None]
        for name in (
            "client_connection_wait_seconds",
            "client_connection_setup_seconds",
            "client_request_write_seconds",
            "client_response_header_seconds",
            "client_response_read_seconds",
        )
    }
    statuses = Counter(str(sample.status_code or "client_error") for sample in samples)
    return {
        "run_id": result.run_id,
        "started_at": result.started_at,
        "target_rate_rps": target_rate,
        "target_count": result.target_count,
        "client_started_count": result.scheduled_count,
        "generator_dropped_count": result.generator_dropped_count,
        "completion_count": len(samples),
        "success_count": len(successes),
        "status_counts": dict(sorted(statuses.items())),
        "error_counts": dict(
            sorted(Counter(sample.error for sample in samples if sample.error).items())
        ),
        "arrival_window_seconds": result.arrival_window_seconds,
        "drain_window_seconds": result.drain_window_seconds,
        "client_started_rps": result.scheduled_count / result.arrival_window_seconds,
        "arrival_window_completion_rps": len(arrival_completions) / result.arrival_window_seconds,
        "max_in_flight_observed": result.max_in_flight_observed,
        "max_tracked_tasks_observed": result.max_tracked_tasks_observed,
        "generator_worker_count": result.generator_worker_count,
        "generator_start_skew_seconds": result.generator_start_skew_seconds,
        "latency_seconds": _percentiles(latencies),
        "scheduling_lag_seconds": _percentiles(schedule_lags),
        "ttft_seconds": _percentiles(ttfts),
        "client_phases": {
            name.removeprefix("client_"): _percentiles(values)
            for name, values in client_phases.items()
        },
    }


def _percentiles(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "p50": None, "p95": None, "p99": None, "max": None}
    ordered = sorted(float(value) for value in values)

    def nearest_rank(percentile: float) -> float:
        index = max(0, math.ceil(percentile * len(ordered)) - 1)
        return ordered[index]

    return {
        "mean": sum(ordered) / len(ordered),
        "p50": nearest_rank(0.50),
        "p95": nearest_rank(0.95),
        "p99": nearest_rank(0.99),
        "max": ordered[-1],
    }


def write_results(
    result: RunResult,
    summary: dict[str, object],
    output_dir: Path,
    *,
    compress: bool = False,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".jsonl.gz" if compress else ".jsonl"
    raw_path = output_dir / f"gateway-load-{result.run_id}{suffix}"
    summary_path = output_dir / f"gateway-load-{result.run_id}-summary.json"
    raw_output = (
        gzip.open(raw_path, "wt", encoding="utf-8")
        if compress
        else raw_path.open("w", encoding="utf-8")
    )
    with raw_output as destination:
        for sample in result.samples:
            destination.write(json.dumps(asdict(sample), sort_keys=True) + "\n")
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return raw_path, summary_path


def serve_artifacts(output_dir: Path, *, port: int, timeout_seconds: float) -> None:
    """Serve one completed result set for bounded diagnostic artifact transfer."""

    class ArtifactHandler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        partial(ArtifactHandler, directory=str(output_dir)),
    )
    server.daemon_threads = True
    server.timeout = min(1.0, timeout_seconds)
    deadline = perf_counter() + timeout_seconds
    try:
        while perf_counter() < deadline:
            server.handle_request()
    finally:
        server.server_close()


def read_results(raw_path: Path, summary_path: Path) -> RunResult:
    """Rehydrate one bounded child-process run without trusting its stdout."""

    if summary_path.stat().st_size > MAX_RESPONSE_BYTES:
        raise ValueError("load generator summary byte budget exceeded")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(summary, dict):
        raise ValueError("load generator summary must be an object")
    target_count = summary.get("target_count")
    completion_count = summary.get("completion_count")
    worker_count = summary.get("generator_worker_count", 1)
    start_skew = summary.get("generator_start_skew_seconds", 0.0)
    if (
        isinstance(target_count, bool)
        or not isinstance(target_count, int)
        or not 0 <= target_count <= 1_000_000
        or isinstance(completion_count, bool)
        or not isinstance(completion_count, int)
        or not 0 <= completion_count <= target_count
        or isinstance(worker_count, bool)
        or not isinstance(worker_count, int)
        or not 1 <= worker_count <= 16
        or isinstance(start_skew, bool)
        or not isinstance(start_skew, (int, float))
        or not 0 <= float(start_skew) <= 60
    ):
        raise ValueError("load generator summary contains invalid counts")
    samples: list[RequestSample] = []
    raw_input = (
        gzip.open(raw_path, "rt", encoding="utf-8")
        if raw_path.name.endswith(".jsonl.gz")
        else raw_path.open(encoding="utf-8")
    )
    with raw_input as source:
        while line := source.readline(4097):
            if len(line) > 4096:
                raise ValueError("load generator sample byte budget exceeded")
            if len(samples) >= target_count:
                raise ValueError("load generator raw sample budget exceeded")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("load generator sample must be an object")
            sample = RequestSample(**value)
            if (
                isinstance(sample.index, bool)
                or not isinstance(sample.index, int)
                or not 0 <= sample.index < target_count
                or not isinstance(sample.request_id, str)
                or len(sample.request_id) > 128
                or (
                    sample.error is not None
                    and (not isinstance(sample.error, str) or len(sample.error) > 128)
                )
                or any(
                    type(number) not in (int, float) or not math.isfinite(number) or number < 0
                    for number in (
                        sample.scheduled_offset_seconds,
                        sample.start_offset_seconds,
                        sample.completion_offset_seconds,
                        sample.scheduling_lag_seconds,
                        sample.latency_seconds,
                    )
                )
                or type(sample.completed_in_arrival_window) is not bool
                or type(sample.bytes_received) is not int
                or not 0 <= sample.bytes_received <= MAX_RESPONSE_BYTES
                or (
                    sample.status_code is not None
                    and (
                        type(sample.status_code) is not int or not 100 <= sample.status_code <= 599
                    )
                )
                or any(
                    number is not None
                    and (
                        type(number) not in (int, float) or not math.isfinite(number) or number < 0
                    )
                    for number in (
                        sample.ttft_seconds,
                        sample.client_connection_wait_seconds,
                        sample.client_connection_setup_seconds,
                        sample.client_request_write_seconds,
                        sample.client_response_header_seconds,
                        sample.client_response_read_seconds,
                    )
                )
            ):
                raise ValueError("load generator sample contains invalid fields")
            samples.append(sample)
    if len(samples) != completion_count:
        raise ValueError("load generator summary and raw sample counts differ")
    if len({sample.index for sample in samples}) != len(samples):
        raise ValueError("load generator sample identity is duplicated")
    scheduled = summary.get("client_started_count")
    dropped = summary.get("generator_dropped_count")
    if (
        isinstance(scheduled, bool)
        or not isinstance(scheduled, int)
        or isinstance(dropped, bool)
        or not isinstance(dropped, int)
        or scheduled != completion_count
        or dropped < 0
        or scheduled + dropped != target_count
    ):
        raise ValueError("load generator target accounting is invalid")
    for name, limit in (
        ("arrival_window_seconds", MAX_DURATION_SECONDS),
        ("drain_window_seconds", 120),
    ):
        value = summary.get(name)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= limit:
            raise ValueError("load generator summary contains invalid time bounds")
    for name in ("max_in_flight_observed", "max_tracked_tasks_observed"):
        value = summary.get(name)
        if type(value) is not int or not 0 <= value <= MAX_IN_FLIGHT:
            raise ValueError("load generator summary contains invalid task bounds")
    return RunResult(
        run_id=str(summary["run_id"]),
        started_at=str(summary["started_at"]),
        target_count=target_count,
        scheduled_count=int(summary["client_started_count"]),
        generator_dropped_count=int(summary["generator_dropped_count"]),
        arrival_window_seconds=float(summary["arrival_window_seconds"]),
        drain_window_seconds=float(summary["drain_window_seconds"]),
        max_in_flight_observed=int(summary["max_in_flight_observed"]),
        max_tracked_tasks_observed=int(summary["max_tracked_tasks_observed"]),
        samples=tuple(samples),
        generator_worker_count=worker_count,
        generator_start_skew_seconds=float(start_skew),
    )


def _load_keys(args: argparse.Namespace) -> list[str]:
    keys = [str(value).strip() for value in args.api_key or [] if str(value).strip()]
    environment_key = os.getenv("DELTALLM_LOAD_API_KEY", "").strip()
    if environment_key:
        keys.append(environment_key)
    if args.api_key_file:
        keys.extend(
            line.strip()
            for line in Path(args.api_key_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    if not keys:
        raise RuntimeError("provide --api-key, --api-key-file, or DELTALLM_LOAD_API_KEY")
    return keys


def _request_function(
    args: argparse.Namespace, keys: list[str]
) -> tuple[httpx.AsyncClient, RequestFunction]:
    urls = [args.url] if isinstance(args.url, str) else list(args.url)
    if not urls or any(not isinstance(url, str) or not url for url in urls):
        raise ValueError("provide at least one workload URL")
    max_keepalive = args.max_keepalive or args.max_in_flight
    if not 1 <= max_keepalive <= args.max_in_flight <= MAX_IN_FLIGHT:
        raise ValueError("HTTP connection limits are invalid")
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 120:
        raise ValueError("HTTP timeout must be between zero and 120 seconds")
    limits = httpx.Limits(
        max_connections=args.max_in_flight,
        max_keepalive_connections=max_keepalive,
    )
    client = httpx.AsyncClient(
        timeout=args.timeout,
        limits=limits,
        verify=not args.insecure,
        trust_env=False,
        follow_redirects=False,
    )

    async def send(index: int, request_id: str) -> RequestResult:
        key = keys[index % len(keys)]
        headers = {"Authorization": f"Bearer {key}", "x-request-id": request_id}
        payload = {
            "model": args.model,
            "messages": [{"role": "user", "content": args.prompt}],
            "max_tokens": args.max_tokens,
            "stream": args.stream,
        }
        if args.bypass_cache:
            payload["metadata"] = {"cache": False}
        phase_trace = ClientPhaseTrace.start()
        started = phase_trace.started
        url = urls[index % len(urls)]
        if not args.stream:
            body = bytearray()
            response_error: str | None = None
            async with client.stream(
                "POST",
                url,
                headers=headers,
                json=payload,
                extensions={"trace": phase_trace},
            ) as response:
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        response_error = "response_too_large"
                        break
                    body.extend(chunk)
            completed = perf_counter()
            return RequestResult(
                status_code=response.status_code,
                error=(
                    response_error
                    or classify_response(
                        response.status_code,
                        bytes(body),
                        expect_fixed_one_token=args.expect_fixed_one_token,
                    )
                ),
                bytes_received=len(body),
                **phase_trace.result_fields(completed=completed),
            )

        first_byte_at: float | None = None
        received = 0
        async with client.stream(
            "POST",
            url,
            headers=headers,
            json=payload,
            extensions={"trace": phase_trace},
        ) as response:
            async for chunk in response.aiter_bytes():
                if chunk and first_byte_at is None:
                    first_byte_at = perf_counter()
                received += len(chunk)
                if received > MAX_RESPONSE_BYTES:
                    return RequestResult(
                        status_code=response.status_code,
                        error="response_too_large",
                        bytes_received=MAX_RESPONSE_BYTES,
                    )
        return RequestResult(
            status_code=response.status_code,
            ttft_seconds=(first_byte_at - started) if first_byte_at is not None else None,
            bytes_received=received,
            **phase_trace.result_fields(completed=perf_counter()),
        )

    return client, send


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        action="append",
        required=True,
        help="Repeatable full chat-completions endpoint URL",
    )
    parser.add_argument("--api-key", action="append", help="Repeatable test API key; never written")
    parser.add_argument("--api-key-file", help="One test API key per line")
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", default="Reply with OK.")
    parser.add_argument("--max-tokens", type=int, default=1)
    parser.add_argument("--mode", choices=("arrival", "wave"), default="arrival")
    parser.add_argument("--rate", type=float, default=50.0)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument(
        "--target-count",
        type=int,
        help="Exact bounded target count used by synchronized generator shards",
    )
    parser.add_argument(
        "--schedule-offset-seconds",
        type=float,
        default=0.0,
        help="Offset the first arrival while preserving the declared arrival window",
    )
    parser.add_argument(
        "--start-at-epoch",
        type=float,
        help="Wait for a shared UTC epoch before starting the arrival window",
    )
    parser.add_argument("--concurrency", type=int, default=300)
    parser.add_argument("--max-in-flight", type=int, default=1_000)
    parser.add_argument(
        "--max-keepalive",
        type=int,
        help="Keep-alive connection limit; defaults to --max-in-flight",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--drain-timeout", type=float, default=DEFAULT_DRAIN_SECONDS)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--bypass-cache", action="store_true")
    parser.add_argument("--expect-fixed-one-token", action="store_true")
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--artifact-server-port", type=int)
    parser.add_argument("--artifact-server-timeout", type=float, default=60.0)
    parser.add_argument("--output-dir", default=".load-results")
    return parser


async def _wait_for_start(start_at_epoch: float | None) -> None:
    if start_at_epoch is None:
        return
    delay = start_at_epoch - time()
    if delay > 60 or delay < -5:
        raise ValueError("shared generator start must be within five seconds past or sixty future")
    if delay > 0:
        await asyncio.sleep(delay)


async def _main(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.artifact_server_port is not None:
        if not 1024 <= args.artifact_server_port <= 65535:
            raise ValueError("artifact server port must be between 1024 and 65535")
        if not 1 <= args.artifact_server_timeout <= 300:
            raise ValueError("artifact server timeout must be between 1 and 300 seconds")
    keys = _load_keys(args)
    client, request = _request_function(args, keys)
    try:
        if args.mode == "wave":
            if (
                args.target_count is not None
                or args.schedule_offset_seconds
                or args.start_at_epoch is not None
            ):
                raise ValueError("shard scheduling options are only valid in arrival mode")
            result = await run_simultaneous_wave(concurrency=args.concurrency, request=request)
            target_rate = None
        else:
            await _wait_for_start(args.start_at_epoch)
            result = await run_constant_arrival(
                rate=args.rate,
                duration_seconds=args.duration,
                max_in_flight=args.max_in_flight,
                request=request,
                drain_timeout_seconds=args.drain_timeout,
                target_count=args.target_count,
                schedule_offset_seconds=args.schedule_offset_seconds,
            )
            target_rate = args.rate
    finally:
        await client.aclose()
    report = summarize(result, target_rate=target_rate)
    raw_path, summary_path = write_results(result, report, output_dir)
    (output_dir / "artifact-manifest.json").write_text(
        json.dumps(
            {"raw": raw_path.name, "summary": summary_path.name},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {**report, "raw_path": str(raw_path), "summary_path": str(summary_path)}, indent=2
        )
    )
    if args.artifact_server_port is not None:
        await asyncio.to_thread(
            serve_artifacts,
            output_dir,
            port=args.artifact_server_port,
            timeout_seconds=args.artifact_server_timeout,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_parser().parse_args())))
