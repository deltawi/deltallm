"""Capacity measurements keep baseline shedding separate from candidate gates."""

from types import SimpleNamespace

import pytest

from tests.performance.capacity_load import assert_valid_arrival


def result(*statuses: int | None, errors: tuple[str | None, ...] = (), dropped: int = 0):
    if not errors:
        errors = (None,) * len(statuses)
    samples = tuple(
        SimpleNamespace(status_code=status, error=error)
        for status, error in zip(statuses, errors, strict=True)
    )
    return SimpleNamespace(generator_dropped_count=dropped, samples=samples)


def test_baseline_allows_controlled_rejections_with_successes() -> None:
    assert_valid_arrival(result(200, 429, 503), allow_controlled_rejections=True)


@pytest.mark.parametrize(
    "run,allow_controlled_rejections",
    [
        (result(200, 503), False),
        (result(503), True),
        (result(200, 500), True),
        (result(200, None, errors=(None, "ReadTimeout")), True),
        (result(200, dropped=1), True),
    ],
)
def test_arrival_rejects_candidate_shedding_or_invalid_baseline_measurements(
    run, allow_controlled_rejections: bool
) -> None:
    with pytest.raises(AssertionError):
        assert_valid_arrival(
            run,
            allow_controlled_rejections=allow_controlled_rejections,
        )
