"""Capacity measurements keep baseline shedding separate from candidate gates."""

from types import SimpleNamespace

import httpx
import pytest

from tests.performance.capacity_load import assert_valid_arrival, rejection_code


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


def test_rejection_code_names_gateway_errors_and_other_responders() -> None:
    gateway = httpx.Response(
        503,
        json={"error": {"code": "spend_persistence_unavailable", "type": "service_unavailable"}},
    )
    edge = httpx.Response(503, text="<html>503 Service Unavailable</html>")
    assert rejection_code(gateway) == "spend_persistence_unavailable"
    assert rejection_code(edge) == "non_gateway_body"


def test_rejection_evidence_keeps_cause_lines_only() -> None:
    from tests.performance.lifecycle_cluster import REJECTION_EVIDENCE

    kept = [
        "[pod/gateway-1/api] telemetry acceptance failed queue=audit phase=lock reason=lock_timeout",
        "LOG:  process 81 still waiting for ExclusiveLock on advisory lock [16384,1,2,2]",
        "LOG:  process 81 acquired ShareLock on transaction 991 after 140.2 ms",
        "ERROR:  canceling statement due to lock timeout",
        "WARNING spend operation failed stage=operation_admission",
        "WARNING billing transaction failed reason=lock_timeout budget_ms=1000",
    ]
    dropped = ['INFO:     10.0.0.1 - "POST /v1/chat/completions HTTP/1.1" 200 OK']
    assert all(REJECTION_EVIDENCE.search(line) for line in kept)
    assert not any(REJECTION_EVIDENCE.search(line) for line in dropped)
