"""Fixed-provider, constant-arrival PR 4 integration profile, including gateway TTFT.

Run: python -m tests.performance.realtime_selector_profile --output-dir <directory>
Uses the same admitted HTTP path with/without selection. Billing and Redis are
fakes; PostgreSQL/Redis integration tests qualify durability separately. This is
not a deployment-capacity certificate or a real-model savings evaluation.
"""

import argparse
import asyncio
from collections import Counter
import json
import logging
from pathlib import Path
from time import perf_counter

import httpx

from scripts.measure_gateway_load import (
    RequestResult,
    run_constant_arrival,
    summarize,
    write_results,
)
from tests.conftest import test_app as app_fixture
from tests.performance.routing_cache_profile import instrument_async_methods, queue_slope
from tests.router.selection.test_realtime import configure
from tests.router.selection.provider_fixtures import response_body
from tests.test_routing_cache_identity import _publish


async def measure(output_dir, *, selector, streaming, independent=False):
    app = await app_fixture.__wrapped__()
    calls, durations = Counter(), Counter()

    async def provider(request):
        data = json.loads(request.content)
        purpose = "selector" if data.get("max_tokens") == 64 else "answer"
        calls[purpose] += 1
        started = perf_counter()
        await asyncio.sleep(0.001)  # Fixed mock provider latency for both profiles.
        durations[purpose] += perf_counter() - started
        if data.get("stream"):
            chunk = {
                "id": "fixed",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": data["model"],
                "choices": [{"index": 0, "delta": {"content": "OK"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 13, "completion_tokens": 1, "total_tokens": 14},
            }
            return httpx.Response(
                200,
                text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200, json=response_body(text='{"lane":"economy"}' if purpose == "selector" else "OK")
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        billing, policy = configure(app, upstream, independent=independent)
        for entry in app.state.model_registry["backing"]:
            entry["model_info"].update(rpm_limit=10000, tpm_limit=100000000)
        if not selector:
            policy.pop("selector")
            for member in policy["members"]:
                member.pop("lane")
        _publish(app, [policy])
        key = next(iter(app.state._test_repo.records.values()))
        key.rpm_limit, key.tpm_limit = 10000, 100000000
        redis_calls, redis_seconds = Counter(), Counter()
        instrument_async_methods(app.state.redis, redis_calls, redis_seconds)
        first_body = {}

        async def observed_app(scope, receive, send):
            request_id = dict(scope["headers"]).get(b"x-request-id", b"").decode()

            async def observed_send(message):
                if message["type"] == "http.response.body" and message.get("body"):
                    first_body.setdefault(request_id, perf_counter())
                await send(message)

            await app(scope, receive, observed_send)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=observed_app), base_url="http://test"
        ) as client:

            async def send(index, request_id):
                started = perf_counter()
                result = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": "Bearer sk-test", "x-request-id": request_id},
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": f"fixed {index}"}],
                        "max_tokens": 1,
                        "stream": streaming,
                    },
                )
                at = first_body.pop(request_id, None)
                return RequestResult(
                    status_code=result.status_code,
                    bytes_received=len(result.content),
                    ttft_seconds=(at - started if streaming and at is not None else None),
                )

            assert (await send(-1, "warmup")).status_code == 200
            calls.clear()
            durations.clear()
            redis_calls.clear()
            redis_seconds.clear()
            billing.reset_mock()
            run = await run_constant_arrival(
                rate=10, duration_seconds=20, max_in_flight=16, request=send
            )
    report = summarize(run, target_rate=10)
    logical = {
        name: getattr(billing, name).await_count
        for name in ("reserve", "dispatch", "accept_selector", "unattempted")
    }
    case = f"{'selector' if selector else 'baseline'}-{'stream' if streaming else 'nonstream'}"
    report.update(
        case=case,
        environment="ASGI; fake billing/Redis; each provider hop fixed 1ms; TTFT observed at first ASGI body",
        billing_operations=logical,
        provider_calls=dict(calls),
        provider_seconds=dict(durations),
        redis_calls=dict(redis_calls),
        **queue_slope(run, 20),
    )
    write_results(run, report, output_dir / case)
    print(json.dumps(report), flush=True)
    assert report["success_count"] == 200 and not report["generator_dropped_count"]
    assert calls["answer"] == 200 and calls["selector"] == (200 if selector else 0)
    assert logical == {
        "reserve": 200 if selector else 0,
        "dispatch": 200 if selector else 0,
        "accept_selector": 200 if selector else 0,
        "unattempted": 0,
    }


async def main(output_dir, *, independent=False):
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("src.services.key_service").setLevel(logging.WARNING)
    for streaming in (False, True):
        for selector in (False, True):
            await measure(
                output_dir, selector=selector, streaming=streaming, independent=independent
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--independent",
        action="store_true",
        help="Use a text-only classifier outside answer membership",
    )
    args = parser.parse_args()
    asyncio.run(main(args.output_dir, independent=args.independent))
