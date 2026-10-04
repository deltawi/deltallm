"""Bounded component probe; run this same file against each checkout via PYTHONPATH.

The CPU input is a fixed native, GIL-releasing calculation injected at Presidio's
inspection boundary. It measures scheduling/ownership, not NLP model throughput.
No network dependencies, production data, credentials or request content are exported.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tracemalloc
from time import perf_counter

from src.callbacks import CallbackManager, CustomLogger, build_standard_logging_payload
from src.guardrails.presidio import PresidioGuardrail


def distribution(values):
    ordered = sorted(values)
    return {
        name: ordered[min(len(ordered) - 1, int((len(ordered) - 1) * q))] if ordered else 0
        for name, q in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99), ("max", 1))
    }


async def monitor(stop, rows, occupancy):
    loop = asyncio.get_running_loop()
    started = loop.time()
    while not stop.is_set():
        due = loop.time() + 0.005
        await asyncio.sleep(0.005)
        rows.append(
            {
                "offset_seconds": loop.time() - started,
                "loop_lag_ms": max(0, loop.time() - due) * 1000,
                "python_retained_bytes": tracemalloc.get_traced_memory()[0],
                **occupancy(),
            }
        )


def logging_payload(index):
    now = datetime.now(UTC)
    return build_standard_logging_payload(
        call_type="completion",
        request_id=f"probe-{index}",
        model="test",
        deployment_model="test",
        request_payload={"messages": [{"role": "user", "content": "x" * 4096 + str(index)}]},
        response_obj={"choices": []},
        user_api_key_dict={},
        start_time=now,
        end_time=now,
        api_base=None,
    )


async def callbacks(bounded):
    if bounded:
        from src.request_work_settings import RequestWorkSettings

        manager = CallbackManager(
            RequestWorkSettings(callback_max_pending=16, callback_max_concurrency=2)
        )
    else:
        manager = CallbackManager()
    release, stop = asyncio.Event(), asyncio.Event()
    rows = []
    executions = 0

    class SlowIntegration(CustomLogger):
        async def async_log_success_event(self, **_):
            nonlocal executions
            executions += 1
            await release.wait()

    manager.register_callback(SlowIntegration())

    def occupancy():
        return {
            "pending": manager.delivery.pending if bounded else len(manager._tasks),
            "charged_bytes": manager.delivery.retained_bytes if bounded else None,
            "executions": executions,
        }

    sampler = asyncio.create_task(monitor(stop, rows, occupancy))
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        for index in range(1000):
            await asyncio.sleep(max(0, started + index / 200 - loop.time()))
            manager.dispatch_success_callbacks(logging_payload(index))
        await asyncio.sleep(0)
        plateau = occupancy()
        release.set()
        await manager.shutdown()
        await asyncio.sleep(0)
        drained = occupancy()
        assert executions > 0
        if not bounded:
            assert executions == 1000
    finally:
        release.set()
        await manager.shutdown()
        stop.set()
        await sampler
    return {
        "offered": 1000,
        "offered_rps": 200,
        "plateau": plateau,
        "drained": drained,
        "loop_lag_ms": distribution([row["loop_lag_ms"] for row in rows]),
        "samples": rows,
    }


async def cpu(bounded):
    pool = None
    if bounded:
        from src.blocking_work import BlockingWorkExecutor

        pool = BlockingWorkExecutor(
            allocation="guardrail",
            workers=2,
            max_pending=8,
            max_bytes=33554432,
            timeout_seconds=5,
            shutdown_seconds=5,
        )
    guardrail = PresidioGuardrail(**({"executor": pool} if pool else {}))

    def calculation(_):
        hashlib.pbkdf2_hmac("sha256", b"fixed-cpu-probe", b"local-fixture", 60000)
        return None

    if bounded:
        guardrail._pre_call = calculation
    else:
        # The base implementation calls detection inline inside its async hook.
        def detect(_):
            calculation(None)
            return []

        guardrail._detect = detect
    data = {"messages": ["fixed-cpu-probe"]}
    loop = asyncio.get_running_loop()
    started = loop.time()
    stop = asyncio.Event()
    samples, rows = [], []
    tasks = []
    sampler = asyncio.create_task(
        monitor(stop, rows, lambda: {"pending": pool.pending if pool else 0})
    )

    async def inspect(index):
        due = started + index / 80
        began = loop.time()
        outcome = "completed"
        try:
            await guardrail.async_pre_call_hook({}, None, data, "completion")
        except Exception as exc:
            if getattr(exc, "code", None) != "gateway_work_unavailable":
                raise
            outcome = "gateway_work_unavailable"
        samples.append(
            {
                "index": index,
                "outcome": outcome,
                "arrival_lateness_ms": (began - due) * 1000,
                "latency_from_due_ms": (loop.time() - due) * 1000,
                "execution_wait_ms": (loop.time() - began) * 1000,
            }
        )

    try:
        for index in range(240):
            await asyncio.sleep(max(0, started + index / 80 - loop.time()))
            tasks.append(asyncio.create_task(inspect(index)))
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if pool:
            await pool.shutdown()
        stop.set()
        await sampler
    return {
        "offered": 240,
        "offered_rps": 80,
        "native_iterations_per_inspection": 60000,
        "outcomes": dict(Counter(row["outcome"] for row in samples)),
        "latency_ms": distribution([row["latency_from_due_ms"] for row in samples]),
        "loop_lag_ms": distribution([row["loop_lag_ms"] for row in rows]),
        "pending_after": pool.pending if pool else 0,
        "charged_bytes_after": pool.retained_bytes if pool else 0,
        "requests": sorted(samples, key=lambda row: row["index"]),
        "samples": rows,
    }


async def measure(mode):
    regex = []
    for length in (5000, 10000, 20000):
        value = "a" * length
        start = perf_counter()
        assert PresidioGuardrail._PATTERN_MAP["EMAIL_ADDRESS"].search(value) is None
        regex.append({"characters": length, "scan_ms": (perf_counter() - start) * 1000})
    tracemalloc.start()
    callback_result = await callbacks(mode == "after")
    tracemalloc.stop()
    tracemalloc.start()
    cpu_result = await cpu(mode == "after")
    tracemalloc.stop()
    digest = hashlib.sha256()
    for path in sorted(Path("src").rglob("*.py")) + [Path("uv.lock")]:
        digest.update(str(path).encode() + b"\0" + path.read_bytes())
    return {
        "qualification": "component_only",
        "mode": mode,
        "server_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_sha256": digest.hexdigest(),
        "python": sys.version.split()[0],
        "callbacks": callback_result,
        "cpu": cpu_result,
        "fallback_email_regex": regex,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("before", "after"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(measure(args.mode))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
