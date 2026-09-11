"""One bounded alternating baseline/head control for shared-host latency drift.

Run the unchanged feature baseline on 59442, PR 2 on 59440, and the local mock on
59441, using the same selector-free profile and disposable dependencies.
"""

import argparse
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path

import httpx

from scripts.measure_gateway_load import (
    RequestResult,
    run_constant_arrival,
    summarize,
    write_results,
)


async def compare(output_dir):
    key = os.environ["DELTALLM_LOAD_API_KEY"]
    async with httpx.AsyncClient(
        timeout=10, limits=httpx.Limits(max_connections=32, max_keepalive_connections=32)
    ) as client:

        async def send(index, request_id):
            port = 59442 if index % 2 == 0 else 59440
            response = await client.post(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "x-request-id": request_id},
                json={
                    "model": "pr2-local-chat",
                    "messages": [{"role": "user", "content": "Reply with OK."}],
                    "max_tokens": 1,
                    "stream": False,
                },
            )
            return RequestResult(
                status_code=response.status_code, bytes_received=len(response.content)
            )

        warmup = await run_constant_arrival(
            rate=10, duration_seconds=10, max_in_flight=32, request=send
        )
        write_results(warmup, summarize(warmup, target_rate=10), output_dir / "paired-warmup")
        run = await run_constant_arrival(
            rate=10, duration_seconds=60, max_in_flight=32, request=send
        )
        report = summarize(run, target_rate=10)
        for index, label in enumerate(("before", "after")):
            samples = tuple(sample for sample in run.samples if sample.index % 2 == index)
            side = replace(run, samples=samples, target_count=300, scheduled_count=len(samples))
            report[label] = summarize(side, target_rate=5)
        write_results(run, report, output_dir / "paired")
        print(json.dumps(report, indent=2), flush=True)
        if report["success_count"] != 600 or report["generator_dropped_count"]:
            raise RuntimeError("paired local profile failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(compare(args.output_dir))
