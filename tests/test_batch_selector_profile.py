import asyncio
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from scripts.measure_gateway_load import run_simultaneous_wave
from tests.performance import batch_selector_profile
from tests.performance.batch_selector_profile import CASES, measure

pytestmark = pytest.mark.app


@pytest.fixture
def serial_slices(monkeypatch):
    async def run(*, rate, duration_seconds, max_in_flight, request):
        # This finite wave tests slice correctness, not CI host throughput. The
        # real profile retains its strict constant-arrival/drop assertions.
        assert max_in_flight == 1
        lock = asyncio.Lock()

        async def send(index, request_id):
            async with lock:
                return await request(index, request_id)

        return await run_simultaneous_wave(concurrency=int(rate * duration_seconds), request=send)

    monkeypatch.setattr(batch_selector_profile, "run_constant_arrival", run)
    return run


@pytest.mark.parametrize("case", CASES)
async def test_batch_profile_covers_multiple_slices_without_hiding_failures(
    tmp_path, case, serial_slices
):
    report = await measure(tmp_path, case=case, duration_seconds=3, rate=1)
    assert report["target_count"] == report["success_count"] == 3
    assert report["generator_dropped_count"] == 0
    assert report["item_count"] == 24
    assert report["answer_quality_score"] is report["real_provider_savings"] is None
    if case == "baseline_microbatch":
        assert report["microbatch_sizes"] == [8, 8, 8]
    if case.startswith("baseline_split"):
        assert report["microbatch_sizes"] == [8, 8, 8]
        assert report["provider_calls"] == {"answer": 24, "microbatch": 3}
        assert report["repository_calls"]["renew_item_lease"] >= 48
        assert report["split_dispatch_renewals"] == 24
        assert report["answer_peak_by_model"] == {"quality": 1}
    else:
        assert report["split_dispatch_renewals"] == 0
    assert report["remaining_caller_leases"] == report["remaining_caller_refreshers"] == 0
    if case.startswith("selector_"):
        assert report["checkpoint_transactions"] == 48
        assert report["billing_operations"]["accept_selector"] == 24
        assert sum(report["selected_lanes"].values()) == 24
    if case == "selector_safe_default":
        assert report["selected_lanes"] == {"quality": 24}
        assert Decimal(report["synthetic_net_savings_exact"]) < 0
    if case == "selector_cap_one":
        assert report["answer_peak_by_model"] == {"quality": 1}


@pytest.mark.parametrize("failure", ["failed_slice", "dropped_slice"])
async def test_batch_profile_still_rejects_failed_or_dropped_load(
    tmp_path, monkeypatch, serial_slices, failure
):
    async def failed_run(**kwargs):
        run = await serial_slices(**kwargs)
        if failure == "dropped_slice":
            return replace(run, generator_dropped_count=1, target_count=run.target_count + 1)
        return replace(
            run,
            samples=(replace(run.samples[0], status_code=500), *run.samples[1:]),
        )

    monkeypatch.setattr(batch_selector_profile, "run_constant_arrival", failed_run)
    with pytest.raises(AssertionError):
        await measure(tmp_path, case="baseline_concurrent", duration_seconds=3, rate=1)
    report = json.loads(next((tmp_path / "baseline_concurrent").glob("*-summary.json")).read_text())
    if failure == "dropped_slice":
        assert report["generator_dropped_count"] == 1
        assert report["success_count"] == 3 < report["target_count"]
    else:
        assert report["status_counts"] == {"200": 2, "500": 1}
        assert report["success_count"] == 2
