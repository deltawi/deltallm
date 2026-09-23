"""Reuse the arrival generator and retain every rejection in the capacity fixture."""

import asyncio
import json
from pathlib import Path
from time import monotonic

import httpx

from scripts.measure_gateway_load import (
    RequestResult,
    run_constant_arrival,
    summarize,
    write_results,
)
from tests.performance.lifecycle_cluster import LOAD_KEY

PAYLOAD = {
    "model": "concurrency-fixture",
    "max_tokens": 1,
    "messages": [{"role": "user", "content": "Reply with OK."}],
    "metadata": {"cache": False},
}


async def arrival(url: str, output: Path, *, rate: float = 10, duration: float = 20) -> dict:
    async with httpx.AsyncClient(
        timeout=10,
        trust_env=False,
        limits=httpx.Limits(max_connections=128, max_keepalive_connections=0),
    ) as client:

        async def request(_index: int, request_id: str) -> RequestResult:
            response = await client.post(
                url + "/v1/chat/completions",
                headers={"Authorization": "Bearer " + LOAD_KEY, "x-request-id": request_id},
                json=PAYLOAD,
            )
            return RequestResult(
                status_code=response.status_code, bytes_received=len(response.content)
            )

        result = await run_constant_arrival(
            rate=rate, duration_seconds=duration, max_in_flight=128, request=request
        )
    summary = summarize(result, target_rate=rate)
    write_results(result, summary, output)
    if result.generator_dropped_count or any(
        sample.status_code != 200 for sample in result.samples
    ):
        raise AssertionError("Controlled capacity arrival workload lost or rejected requests")
    return summary


class HeldStreams:
    """A bounded owner; task cancellation closes each HTTP response before exit."""

    def __init__(self, url: str, output: Path) -> None:
        self.url, self.output = url, output
        self.release = asyncio.Event()
        self.tasks: list[asyncio.Task] = []
        self.events: list[dict] = []
        self.active = 0
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(360, connect=5, pool=5),
            trust_env=False,
            limits=httpx.Limits(max_connections=400, max_keepalive_connections=0),
        )

    async def launch(self, count: int) -> None:
        if len(self.tasks) + count > 400:
            raise RuntimeError("Stream experiment task bound exceeded")
        for _ in range(count):
            index = len(self.tasks)
            self.tasks.append(asyncio.create_task(self._stream(index)))
            await asyncio.sleep(0.1)

    async def _stream(self, index: int) -> None:
        started = monotonic()
        try:
            async with self.client.stream(
                "POST",
                self.url + "/v1/chat/completions",
                headers={"Authorization": "Bearer " + LOAD_KEY},
                json={**PAYLOAD, "stream": True},
            ) as response:
                if response.status_code != 200:
                    self.events.append({"index": index, "status": response.status_code})
                    return
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        break
                else:
                    raise RuntimeError("Fixture stream had no first frame")
                self.active += 1
                self.events.append(
                    {"index": index, "status": 200, "ttft_seconds": monotonic() - started}
                )
                try:
                    await self.release.wait()
                    # Avoid making cleanup itself an unrelated simultaneous burst.
                    await asyncio.sleep(index * 0.02)
                finally:
                    self.active -= 1
        except (httpx.HTTPError, RuntimeError) as error:
            self.events.append({"index": index, "error": type(error).__name__})

    async def close(self) -> None:
        self.release.set()
        try:
            async with asyncio.timeout(20):
                await asyncio.gather(*self.tasks)
        finally:
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            await self.client.aclose()
            self.output.write_text(json.dumps(self.events, indent=2) + "\n")
