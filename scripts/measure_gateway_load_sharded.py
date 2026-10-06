#!/usr/bin/env python3
"""Coordinate bounded load-generator processes and publish one merged result."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import sys
from time import time

try:
    from scripts.measure_gateway_load import (
        RunResult,
        merge_run_results,
        read_results,
        serve_artifacts,
        summarize,
        write_results,
        MAX_RATE,
        MAX_DURATION_SECONDS,
        MAX_SAMPLES,
        MAX_IN_FLIGHT,
    )
except ModuleNotFoundError:
    from measure_gateway_load import (
        RunResult,
        merge_run_results,
        read_results,
        serve_artifacts,
        summarize,
        write_results,
        MAX_RATE,
        MAX_DURATION_SECONDS,
        MAX_SAMPLES,
        MAX_IN_FLIGHT,
    )

_RESULT_NAME = re.compile(r"gateway-load-([0-9a-f]{32})\.jsonl(?:\.gz)?")
_SUMMARY_NAME = re.compile(r"gateway-load-([0-9a-f]{32})-summary\.json")
_MAX_WORKERS = 16
_MAX_CHILD_OUTPUT_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class ShardSpec:
    index: int
    url: str
    target_count: int
    rate: float
    schedule_offset_seconds: float
    max_in_flight: int
    max_keepalive: int


def build_shard_specs(
    *,
    urls: list[str],
    workers: int,
    rate: float,
    duration: float,
    max_in_flight: int,
    max_keepalive: int,
) -> tuple[ShardSpec, ...]:
    if (
        not urls
        or any(not isinstance(url, str) or not url for url in urls)
        or any(type(value) is not int for value in (workers, max_in_flight, max_keepalive))
        or type(rate) not in (int, float)
        or type(duration) not in (int, float)
        or not 1 <= workers <= _MAX_WORKERS
        or not math.isfinite(rate)
        or not 0 < rate <= MAX_RATE
        or not math.isfinite(duration)
        or not 0 < duration <= MAX_DURATION_SECONDS
        or not workers <= max_in_flight <= MAX_IN_FLIGHT
        or max_keepalive < workers
        or max_keepalive > max_in_flight
    ):
        raise ValueError("sharded generator topology is invalid")
    total_target = max(1, math.floor(rate * duration))
    if total_target > MAX_SAMPLES:
        raise ValueError("sharded generator sample budget exceeded")
    worker_count = min(workers, total_target)
    specs: list[ShardSpec] = []
    for index in range(worker_count):
        target_count = ((total_target - 1 - index) // worker_count) + 1
        specs.append(
            ShardSpec(
                index=index,
                url=urls[index % len(urls)],
                target_count=target_count,
                rate=rate / worker_count,
                schedule_offset_seconds=index / rate,
                max_in_flight=_share(max_in_flight, worker_count, index),
                max_keepalive=_share(max_keepalive, worker_count, index),
            )
        )
    return tuple(specs)


def _share(total: int, workers: int, index: int) -> int:
    return (total // workers) + (1 if index < total % workers else 0)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", action="append", required=True)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", default="Reply with OK.")
    parser.add_argument("--max-tokens", type=int, default=1)
    parser.add_argument("--rate", type=float, required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--max-in-flight", type=int, default=1_000)
    parser.add_argument("--max-keepalive", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--drain-timeout", type=float, default=60)
    parser.add_argument("--bypass-cache", action="store_true")
    parser.add_argument("--expect-fixed-one-token", action="store_true")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--artifact-server-port", type=int)
    parser.add_argument("--artifact-server-timeout", type=float, default=60)
    parser.add_argument("--start-delay", type=float, default=5)
    return parser


def _child_command(
    args: argparse.Namespace,
    spec: ShardSpec,
    *,
    start_at_epoch: float,
    output: Path,
) -> tuple[str, ...]:
    command = [
        sys.executable,
        str(Path(__file__).with_name("measure_gateway_load.py")),
        "--url",
        spec.url,
        "--model",
        args.model,
        "--prompt",
        args.prompt,
        "--max-tokens",
        str(args.max_tokens),
        "--rate",
        str(spec.rate),
        "--duration",
        str(args.duration),
        "--target-count",
        str(spec.target_count),
        "--schedule-offset-seconds",
        str(spec.schedule_offset_seconds),
        "--start-at-epoch",
        str(start_at_epoch),
        "--max-in-flight",
        str(spec.max_in_flight),
        "--max-keepalive",
        str(spec.max_keepalive),
        "--timeout",
        str(args.timeout),
        "--output-dir",
        str(output),
    ]
    if args.drain_timeout is not None:
        command.extend(("--drain-timeout", str(args.drain_timeout)))
    if args.bypass_cache:
        command.append("--bypass-cache")
    if args.expect_fixed_one_token:
        command.append("--expect-fixed-one-token")
    return tuple(command)


def _read_worker_result(output: Path) -> RunResult:
    with (output / "artifact-manifest.json").open("rb") as source:
        data = source.read(4097)
    if len(data) > 4096:
        raise RuntimeError("generator worker artifact manifest byte budget exceeded")
    manifest = json.loads(data)
    raw_name = manifest.get("raw") if isinstance(manifest, dict) else None
    summary_name = manifest.get("summary") if isinstance(manifest, dict) else None
    raw_match = _RESULT_NAME.fullmatch(raw_name) if isinstance(raw_name, str) else None
    summary_match = _SUMMARY_NAME.fullmatch(summary_name) if isinstance(summary_name, str) else None
    if raw_match is None or summary_match is None or raw_match.group(1) != summary_match.group(1):
        raise RuntimeError("generator worker artifact manifest is invalid")
    return read_results(output / raw_name, output / summary_name)


async def _run_worker(
    args: argparse.Namespace,
    spec: ShardSpec,
    *,
    start_at_epoch: float,
    output: Path,
) -> RunResult:
    output.mkdir(parents=True)
    process = await asyncio.create_subprocess_exec(
        *_child_command(
            args,
            spec,
            start_at_epoch=start_at_epoch,
            output=output,
        ),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def bounded_output(stream: asyncio.StreamReader) -> bytes:
        chunks = bytearray()
        while chunk := await stream.read(8192):
            if len(chunks) + len(chunk) > _MAX_CHILD_OUTPUT_BYTES // 2:
                raise RuntimeError("generator worker process output byte budget exceeded")
            chunks.extend(chunk)
        return bytes(chunks)

    try:
        assert process.stdout is not None and process.stderr is not None
        async with asyncio.timeout(args.start_delay + args.duration + args.drain_timeout + 15):
            async with asyncio.TaskGroup() as group:
                stdout_task = group.create_task(bounded_output(process.stdout))
                stderr_task = group.create_task(bounded_output(process.stderr))
                group.create_task(process.wait())
        (output / "worker.log").write_bytes(stdout_task.result() + stderr_task.result())
        if process.returncode:
            raise RuntimeError(
                f"generator worker {spec.index} failed with exit {process.returncode}"
            )
        return _read_worker_result(output)
    finally:
        if process.returncode is None:
            process.kill()

        async def discard(stream: asyncio.StreamReader) -> None:
            count = 0
            while chunk := await stream.read(8192):
                count += len(chunk)
                if count > _MAX_CHILD_OUTPUT_BYTES * 4:
                    raise RuntimeError("generator pipe cleanup byte budget exceeded")

        if process.stdout is not None and process.stderr is not None:
            try:
                async with asyncio.timeout(5):
                    async with asyncio.TaskGroup() as group:
                        group.create_task(discard(process.stdout))
                        group.create_task(discard(process.stderr))
                        group.create_task(process.wait())
            except TimeoutError:
                raise RuntimeError("generator process cleanup did not finish") from None


async def _main(args: argparse.Namespace) -> int:
    if args.artifact_server_port is not None:
        if not 1024 <= args.artifact_server_port <= 65535:
            raise ValueError("artifact server port must be between 1024 and 65535")
        if not 1 <= args.artifact_server_timeout <= 300:
            raise ValueError("artifact server timeout must be between one and 300 seconds")
    output = Path(args.output_dir)
    if output.exists() and any(output.iterdir()):
        raise ValueError("sharded generator output directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    workers = args.workers if args.workers is not None else len(args.url)
    specs = build_shard_specs(
        urls=list(args.url),
        workers=workers,
        rate=args.rate,
        duration=args.duration,
        max_in_flight=args.max_in_flight,
        max_keepalive=args.max_keepalive,
    )
    if not 1 <= args.start_delay <= 30:
        raise ValueError("generator shard start delay must be between one and thirty seconds")
    start_at_epoch = time() + args.start_delay
    async with asyncio.TaskGroup() as group:
        tasks = [
            group.create_task(
                _run_worker(
                    args,
                    spec,
                    start_at_epoch=start_at_epoch,
                    output=output / f"worker-{spec.index}",
                )
            )
            for spec in specs
        ]
    results = [task.result() for task in tasks]
    merged = merge_run_results(results)
    report = summarize(merged, target_rate=args.rate)
    raw_path, summary_path = write_results(merged, report, output, compress=True)
    (output / "artifact-manifest.json").write_text(
        json.dumps(
            {
                "raw": raw_path.name,
                "summary": summary_path.name,
                "workers": len(specs),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({**report, "raw_path": str(raw_path), "summary_path": str(summary_path)}))
    if args.artifact_server_port is not None:
        await asyncio.to_thread(
            serve_artifacts,
            output,
            port=args.artifact_server_port,
            timeout_seconds=args.artifact_server_timeout,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_parser().parse_args())))
