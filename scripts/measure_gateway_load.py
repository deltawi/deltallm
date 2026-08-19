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
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx


@dataclass(frozen=True, slots=True)
class RequestResult:
    status_code: int | None
    error: str | None = None
    ttft_seconds: float | None = None
    bytes_received: int = 0


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


RequestFunction = Callable[[int, str], Awaitable[RequestResult]]


async def run_constant_arrival(
    *,
    rate: float,
    duration_seconds: float,
    max_in_flight: int,
    request: RequestFunction,
) -> RunResult:
    if rate <= 0 or duration_seconds <= 0 or max_in_flight <= 0:
        raise ValueError("rate, duration_seconds, and max_in_flight must be positive")

    run_id = uuid4().hex
    started_at = datetime.now(tz=UTC).isoformat()
    target_count = max(1, int(math.floor(rate * duration_seconds)))
    interval = 1.0 / rate
    started = perf_counter()
    arrival_deadline = started + duration_seconds
    active = 0
    max_active = 0
    dropped = 0
    tasks: set[asyncio.Task[RequestSample]] = set()

    async def execute(index: int, request_id: str, scheduled_at: float) -> RequestSample:
        nonlocal active
        actual_start = perf_counter()
        try:
            result = await request(index, request_id)
        except Exception as exc:  # the harness must retain every client-side failure
            result = RequestResult(status_code=None, error=exc.__class__.__name__)
        completed = perf_counter()
        active -= 1
        return RequestSample(
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
        )

    try:
        for index in range(target_count):
            scheduled_at = started + (index * interval)
            delay = scheduled_at - perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            if active >= max_in_flight:
                dropped += 1
                continue
            active += 1
            max_active = max(max_active, active)
            request_id = f"load-{run_id}-{index}"
            task = asyncio.create_task(execute(index, request_id, scheduled_at))
            tasks.add(task)

        remaining = arrival_deadline - perf_counter()
        if remaining > 0:
            await asyncio.sleep(remaining)
        drain_started = perf_counter()
        samples = tuple(await asyncio.gather(*tasks)) if tasks else ()
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finished = perf_counter()
    return RunResult(
        run_id=run_id,
        started_at=started_at,
        target_count=target_count,
        scheduled_count=len(tasks),
        generator_dropped_count=dropped,
        arrival_window_seconds=duration_seconds,
        drain_window_seconds=max(0.0, finished - drain_started),
        max_in_flight_observed=max_active,
        samples=tuple(sorted(samples, key=lambda item: item.index)),
    )


async def run_simultaneous_wave(
    *,
    concurrency: int,
    request: RequestFunction,
) -> RunResult:
    if concurrency <= 0:
        raise ValueError("concurrency must be positive")
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
        )

    tasks = [asyncio.create_task(execute(index)) for index in range(concurrency)]
    try:
        await all_ready.wait()
        wave_started = perf_counter()
        gate.set()
        samples = tuple(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
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
        samples=tuple(sorted(samples, key=lambda item: item.index)),
    )


def summarize(result: RunResult, *, target_rate: float | None) -> dict[str, Any]:
    samples = list(result.samples)
    successes = [
        sample
        for sample in samples
        if sample.status_code is not None and 200 <= sample.status_code < 300
    ]
    arrival_completions = [sample for sample in samples if sample.completed_in_arrival_window]
    latencies = [sample.latency_seconds for sample in samples]
    schedule_lags = [sample.scheduling_lag_seconds for sample in samples]
    ttfts = [sample.ttft_seconds for sample in samples if sample.ttft_seconds is not None]
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
        "arrival_window_seconds": result.arrival_window_seconds,
        "drain_window_seconds": result.drain_window_seconds,
        "client_started_rps": result.scheduled_count / result.arrival_window_seconds,
        "arrival_window_completion_rps": len(arrival_completions) / result.arrival_window_seconds,
        "max_in_flight_observed": result.max_in_flight_observed,
        "latency_seconds": _percentiles(latencies),
        "scheduling_lag_seconds": _percentiles(schedule_lags),
        "ttft_seconds": _percentiles(ttfts),
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
    result: RunResult, summary: dict[str, Any], output_dir: Path
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / f"gateway-load-{result.run_id}.jsonl"
    summary_path = output_dir / f"gateway-load-{result.run_id}-summary.json"
    raw_path.write_text(
        "".join(json.dumps(asdict(sample), sort_keys=True) + "\n" for sample in result.samples),
        encoding="utf-8",
    )
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return raw_path, summary_path


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
    limits = httpx.Limits(
        max_connections=args.max_in_flight,
        max_keepalive_connections=args.max_in_flight,
    )
    client = httpx.AsyncClient(timeout=args.timeout, limits=limits, verify=not args.insecure)

    async def send(index: int, request_id: str) -> RequestResult:
        key = keys[index % len(keys)]
        headers = {"Authorization": f"Bearer {key}", "x-request-id": request_id}
        payload = {
            "model": args.model,
            "messages": [{"role": "user", "content": args.prompt}],
            "max_tokens": args.max_tokens,
            "stream": args.stream,
        }
        started = perf_counter()
        if not args.stream:
            response = await client.post(args.url, headers=headers, json=payload)
            return RequestResult(
                status_code=response.status_code,
                bytes_received=len(response.content),
            )

        first_byte_at: float | None = None
        received = 0
        async with client.stream("POST", args.url, headers=headers, json=payload) as response:
            async for chunk in response.aiter_bytes():
                if chunk and first_byte_at is None:
                    first_byte_at = perf_counter()
                received += len(chunk)
        return RequestResult(
            status_code=response.status_code,
            ttft_seconds=(first_byte_at - started) if first_byte_at is not None else None,
            bytes_received=received,
        )

    return client, send


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Full chat-completions endpoint URL")
    parser.add_argument("--api-key", action="append", help="Repeatable test API key; never written")
    parser.add_argument("--api-key-file", help="One test API key per line")
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", default="Reply with OK.")
    parser.add_argument("--max-tokens", type=int, default=1)
    parser.add_argument("--mode", choices=("arrival", "wave"), default="arrival")
    parser.add_argument("--rate", type=float, default=50.0)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--concurrency", type=int, default=300)
    parser.add_argument("--max-in-flight", type=int, default=1_000)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--output-dir", default=".load-results")
    return parser


async def _main(args: argparse.Namespace) -> int:
    keys = _load_keys(args)
    client, request = _request_function(args, keys)
    try:
        if args.mode == "wave":
            result = await run_simultaneous_wave(concurrency=args.concurrency, request=request)
            target_rate = None
        else:
            result = await run_constant_arrival(
                rate=args.rate,
                duration_seconds=args.duration,
                max_in_flight=args.max_in_flight,
                request=request,
            )
            target_rate = args.rate
    finally:
        await client.aclose()
    report = summarize(result, target_rate=target_rate)
    raw_path, summary_path = write_results(result, report, Path(args.output_dir))
    print(
        json.dumps(
            {**report, "raw_path": str(raw_path), "summary_path": str(summary_path)}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_parser().parse_args())))
