import pytest
from decimal import Decimal

from tests.performance.batch_selector_profile import CASES, measure

pytestmark = pytest.mark.app


@pytest.mark.parametrize("case", CASES)
async def test_batch_profile_covers_multiple_slices_without_hiding_failures(tmp_path, case):
    report = await measure(tmp_path, case=case, duration_seconds=3, rate=1)
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
