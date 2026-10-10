"""Capacity acceptance assertions distinguish edge shedding from admitted streams."""

import pytest

from tests.performance.capacity_scaling import assert_bounded_overload


def test_bounded_overload_allows_only_expected_admitted_stream_timeouts() -> None:
    assert assert_bounded_overload(
        [
            {"status": 429, "seconds": 0.3},
            {"status": 503, "seconds": 0.4},
            {"error": "ReadTimeout", "seconds": 6.3},
        ]
    ) == (2, 1)


@pytest.mark.parametrize(
    "results",
    [
        [{"status": 503, "seconds": 2.0}],
        [{"status": 503, "seconds": 0.4}, {"error": "ConnectError", "seconds": 0.1}],
        [{"error": "ReadTimeout", "seconds": 6.3}],
    ],
)
def test_bounded_overload_rejects_slow_or_unexplained_results(results: list[dict]) -> None:
    with pytest.raises(AssertionError):
        assert_bounded_overload(results)
