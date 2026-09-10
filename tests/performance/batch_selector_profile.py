"""PR 6 fixed-provider Batch throughput/cost tradeoff, without live paid models.

Run: python -m tests.performance.batch_selector_profile --output-dir <directory>
Each constant-arrival sample is one eight-item worker slice, not an HTTP request.
SQL/Redis and claims are fakes; separate dependency lanes qualify durability.
The adapter mock takes 5 ms per provider call, including a whole microbatch.
Prices and lane labels are synthetic; answer quality and real savings are unknown.
"""

import argparse
import asyncio
from collections import Counter
from decimal import Decimal
import json
import logging
from pathlib import Path
from time import perf_counter

import pytest

from scripts.measure_gateway_load import (
    RequestResult,
    run_constant_arrival,
    summarize,
    write_results,
)
from tests.batch.selector_fixtures import selected_batch_harness
from tests.conftest import test_app as app_fixture
from tests.performance.routing_cache_profile import instrument_async_methods, queue_slope
from tests.router.selection.provider_fixtures import response_body
from tests.test_routing_cache_identity import _publish
from src.models.errors import ServiceUnavailableError

SLICE_SIZE = 8
CASES = (
    "baseline_concurrent",
    "baseline_microbatch",
    "baseline_split",
    "baseline_split_slow",
    "selector_individual",
    "selector_balanced",
    "selector_quality_heavy",
    "selector_safe_default",
    "selector_cap_one",
)


class FixedMicrobatch:
    def __init__(self, *, unsupported=False):
        self.sizes = []
        self.provider_seconds = 0.0
        self.unsupported = unsupported

    async def execute_chat_microbatch(self, *, requests, deployment, request_context):
        self.sizes.append(len(requests))
        started = perf_counter()
        await asyncio.sleep(0.005)
        self.provider_seconds += perf_counter() - started
        if self.unsupported:
            raise ServiceUnavailableError(
                code="chat_microbatch_unsupported", affects_deployment_health=False
            )
        return [
            {
                "index": index,
                "response_body": response_body(text="answer"),
                "usage": {"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18},
            }
            for index in range(len(requests))
        ]


async def measure(output_dir, *, case, duration_seconds=10, rate=2, independent=False):
    app = await app_fixture.__wrapped__()
    with pytest.MonkeyPatch.context() as patch:
        fixture = selected_batch_harness(app, patch, independent=independent)
        h = await anext(fixture)
        try:
            return await _measure(h, output_dir, case, duration_seconds, rate)
        finally:
            await fixture.aclose()


async def _measure(h, output_dir, case, duration_seconds, rate):
    if case not in CASES:
        raise ValueError("unknown Batch profile")
    selected = case.startswith("selector_")
    quality_tenths = {
        "selector_balanced": 5,
        "selector_quality_heavy": 9,
        "selector_cap_one": 10,
    }.get(case, 3)
    if case == "selector_safe_default":
        h.selector_reply = "invalid result"
    batching = "concurrent" if case == "baseline_concurrent" else "sync_microbatch"
    h.worker.config.worker_concurrency = 4
    for record in h.app.state._test_repo.records.values():
        record.rpm_limit, record.tpm_limit, record.max_parallel_requests = 10000, 100000000, 100
        await h.app.state.key_service.invalidate_key_cache_by_hash(record.token)
        await h.app.state.key_service.get_auth_by_token_hash(record.token)
    for entry in h.app.state.model_registry["backing"]:
        info = entry["model_info"]
        info.update(rpm_limit=10000, tpm_limit=100000000)
        if entry["deployment_id"] == "quality":
            info.update(input_cost_per_token="0.00001", output_cost_per_token="0.00002")
        entry["deltallm_params"]["chat_batching"] = {
            "mode": batching,
            "upstream_max_batch_size": SLICE_SIZE,
        }
        if case == "selector_cap_one":
            entry["deltallm_params"]["chat_batching"]["max_in_flight"] = 1
    if not selected:
        h.policy.pop("selector")
        h.policy["members"] = [{"deployment_id": "quality"}]
    _publish(h.app, [h.policy])
    microbatch = FixedMicrobatch(unsupported=case.startswith("baseline_split"))
    h.app.state.chat_microbatch_executor = microbatch
    provider = h.provider
    provider_seconds = Counter()
    answer_active, answer_peak = Counter(), Counter()
    item_samples = []
    pending_times = {}
    original_complete = h.repository.mark_item_completed
    redis_calls, redis_seconds = Counter(), Counter()
    instrument_async_methods(h.app.state.redis, redis_calls, redis_seconds)
    repository_calls, repository_seconds = Counter(), Counter()
    instrument_async_methods(h.repository, repository_calls, repository_seconds)
    renew = h.repository.renew_item_lease
    split_dispatch_renewals = 0
    acquired_items = {}
    engine = h.worker._execution_engine
    acquire = engine._acquire_prepared_policy_lease

    async def counted_renew(**kwargs):
        nonlocal split_dispatch_renewals
        if kwargs.get("expires_at") is not None:
            split_dispatch_renewals += 1
        return await renew(**kwargs)

    async def tracked_acquire(*, prepared):
        await acquire(prepared=prepared)
        acquired_items[prepared.item.item_id] = prepared

    h.repository.renew_item_lease = counted_renew
    engine._acquire_prepared_policy_lease = tracked_acquire

    async def fixed_provider(request):
        body = json.loads(request.content)
        kind = "selector" if body.get("max_tokens") == 64 else "answer"
        model = body["model"]
        if kind == "answer":
            answer_active[model] += 1
            answer_peak[model] = max(answer_peak[model], answer_active[model])
        started = perf_counter()
        try:
            await asyncio.sleep(0.04 if case == "baseline_split_slow" else 0.005)
            return await provider(request)
        finally:
            provider_seconds[kind] += perf_counter() - started
            if kind == "answer":
                answer_active[model] -= 1

    async def completed(**kwargs):
        item_samples.append(
            {
                "item_id": kwargs["item_id"],
                "latency_ms": (perf_counter() - pending_times.pop(kwargs["item_id"])) * 1000,
            }
        )
        return await original_complete(**kwargs)

    h.provider, h.repository.mark_item_completed = fixed_provider, completed

    async def send(index, request_id):
        before = len(h.repository.completed_calls)
        items = [
            h.item(
                index * SLICE_SIZE + offset + 1,
                "complex reasoning"
                if (index * SLICE_SIZE + offset) % 10 < quality_tenths
                else "routine extraction",
            )
            for offset in range(SLICE_SIZE)
        ]
        started = perf_counter()
        for item in items:
            pending_times[item.item_id] = started
        await h.worker._process_items(h.job, items)
        count = len(h.repository.completed_calls) - before
        return RequestResult(status_code=200 if count == SLICE_SIZE else 500)

    run = await run_constant_arrival(
        rate=rate, duration_seconds=duration_seconds, max_in_flight=1, request=send
    )
    report = summarize(run, target_rate=rate)
    provider_counts = Counter(
        "selector" if call.get("max_tokens") == 64 else "answer" for call in h.calls
    )
    provider_counts["microbatch"] = len(microbatch.sizes)
    amounts = {
        "selector": sum(
            (call.args[1].provider_cost for call in h.billing.accept_selector.call_args_list),
            Decimal(0),
        ),
        "answer": sum(
            (Decimal(str(row["provider_cost"])) for row in h.repository.completed_calls), Decimal(0)
        ),
    }
    count = len(h.repository.completed_calls)
    baseline_cost = Decimal("0.00023") * count
    latencies = sorted(row["latency_ms"] for row in item_samples)
    report.update(
        case=case,
        basis="fixed_local_provider_synthetic_prices_no_quality_claim",
        item_count=count,
        items_per_slice=SLICE_SIZE,
        worker_slots=4,
        offered_items_per_second=rate * SLICE_SIZE,
        received_items_per_second=count / duration_seconds,
        provider_calls=dict(provider_counts),
        answer_peak_by_model=dict(answer_peak),
        provider_seconds=dict(provider_seconds),
        microbatch_provider_seconds=microbatch.provider_seconds,
        microbatch_sizes=microbatch.sizes,
        checkpoint_transactions=len(h.checkpoints.writes),
        selected_lanes=dict(
            Counter(
                checkpoint.decision.lane
                for checkpoint in h.checkpoints.rows.values()
                if checkpoint.decision is not None
            )
        ),
        decision_causes=dict(
            Counter(
                checkpoint.decision.cause.value
                for checkpoint in h.checkpoints.rows.values()
                if checkpoint.decision is not None
            )
        ),
        billing_operations={
            name: getattr(h.billing, name).await_count
            for name in ("reserve", "dispatch", "accept_selector")
        },
        redis_calls=dict(redis_calls),
        repository_calls=dict(repository_calls),
        split_dispatch_renewals=split_dispatch_renewals,
        remaining_caller_leases=sum(
            item.policy_lease is not None for item in acquired_items.values()
        ),
        remaining_caller_refreshers=sum(
            item.policy_lease_refresher is not None for item in acquired_items.values()
        ),
        item_latency_ms={
            f"p{p}": latencies[min(len(latencies) - 1, int(len(latencies) * p / 100))]
            for p in (50, 95, 99)
        },
        selector_provider_cost_exact=str(amounts["selector"]),
        answer_provider_cost_exact=str(amounts["answer"]),
        baseline_answer_provider_cost_exact=str(baseline_cost),
        measurable_mock_penalty_exact="0",
        synthetic_net_savings_exact=str(baseline_cost - sum(amounts.values())),
        answer_quality_score=None,
        real_provider_savings=None,
        **queue_slope(run, duration_seconds),
    )
    write_results(run, report, output_dir / case)
    # Raw item timings complement the standard raw constant-arrival slice samples.
    (output_dir / case / "items.json").write_text(json.dumps(item_samples, indent=2) + "\n")
    print(json.dumps(report), flush=True)
    assert report["success_count"] == run.target_count and not run.generator_dropped_count
    assert count == run.target_count * SLICE_SIZE
    assert provider_counts["selector"] == (count if selected else 0)
    assert len(h.checkpoints.writes) == (count * 2 if selected else 0)
    assert not selected or not microbatch.sizes
    assert not report["remaining_caller_leases"] and not report["remaining_caller_refreshers"]
    if case == "selector_cap_one":
        assert answer_peak == {"quality": 1}
    return report


async def main(output_dir, cases, *, independent=False):
    logging.getLogger().setLevel(logging.WARNING)
    for case in cases:
        await measure(output_dir, case=case, independent=independent)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=CASES)
    parser.add_argument(
        "--independent", action="store_true", help="Use a text-only external classifier"
    )
    args = parser.parse_args()
    asyncio.run(main(args.output_dir, args.cases, independent=args.independent))
