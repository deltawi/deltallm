import pytest

from scripts.benchmarks.measure_admission import Sample, coalescing_opportunity

pytestmark = pytest.mark.hermetic


def test_sparse_and_mixed_arrivals_do_not_claim_unrealized_batching_benefit():
    sparse = [Sample(i, 0, i / 50) for i in range(50)]
    mixed = [Sample(i, i % 13, i / 1000) for i in range(1000)]
    assert coalescing_opportunity(sparse)["commits_saved"] == 0
    assert coalescing_opportunity(mixed)["commits_saved"] == 0


def test_coalescing_estimate_respects_tenant_window_and_event_bound():
    burst = [Sample(i, i % 2, 0) for i in range(130)]
    result = coalescing_opportunity(burst)
    assert result["optimistic_events_per_commit"] == 130 / 6
    at_deadline = [Sample(0, 0, 0), Sample(1, 0, 0.002)]
    assert coalescing_opportunity(at_deadline)["commits_saved"] == 0


def test_existing_bundles_count_events_and_preserve_whole_calls():
    burst = [Sample(i, 0, 0) for i in range(32)]
    result = coalescing_opportunity(burst, events_per_call=2)
    assert result["optimistic_events_per_commit"] == 32
    assert result["commits_saved"] == 30
