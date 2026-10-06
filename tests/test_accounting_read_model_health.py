"""Old or incomplete reporting work never becomes healthy empty work."""

import pytest

from src.billing.accounting_read_model_health import ReadModelHealth, ReadModelProgress
from src.bootstrap.accounting_roles import AccountingProcessors
from src.db.accounting_calls import AccountingProtocolUnavailable
from src.telemetry.lifecycle import WorkerState
from src.metrics.accounting_read_models import (
    read_model_progress_available,
    read_model_pending_partitions,
    read_model_oldest_head_age,
    read_model_observed_timestamp,
)
from tests.test_accounting_presence import Processing


def progress(**changes):
    return ReadModelProgress(
        **{
            "generation": 7,
            "partition_count": 4,
            "slots": 4,
            "pending_partitions": 0,
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"generation": True},
        {"slots": True},
        {"pending_partitions": True},
        {"slots": 5},
        {"pending_partitions": 1},
        {"oldest_head_age_seconds": 0.0},
        {"pending_partitions": 1, "oldest_head_age_seconds": float("nan")},
    ],
)
def test_bad_progress_cannot_invent_health(changes):
    with pytest.raises(ValueError):
        progress(**changes)


def test_actual_empty_progress_ready_and_failure_preserves_last_observation():
    clock = [100.0]
    owner = ReadModelHealth(7, clock=lambda: clock[0])
    assert owner.worker_health.state is WorkerState.STARTING
    value = progress()
    owner.observe(value, observed_at=100.0)
    assert owner.worker_health.state is WorkerState.READY
    owner.unavailable()
    assert owner.progress == value
    assert owner.worker_health.detail == "read_model_progress_unavailable"
    owner.observe(progress(slots=3), observed_at=100.0)
    assert owner.worker_health.state is WorkerState.FAILED
    assert owner.worker_health.detail == "read_model_cells_missing"


@pytest.mark.parametrize("elapsed", [5, -1, float("nan"), float("inf")])
def test_stale_or_invalid_clock_never_proves_reporting_ready(elapsed):
    clock = [100.0]
    owner = ReadModelHealth(7, clock=lambda: clock[0])
    owner.observe(progress(), observed_at=100.0)
    clock[0] += elapsed
    assert owner.worker_health.detail == "read_model_progress_stale"


def test_old_report_debt_is_unready_until_an_actual_fresh_catch_up_observation():
    clock = [100.0]
    owner = ReadModelHealth(7, clock=lambda: clock[0])
    owner.observe(progress(pending_partitions=1, oldest_head_age_seconds=59.0), observed_at=100.0)
    assert owner.worker_health.state is WorkerState.READY
    clock[0] += 2
    assert owner.worker_health.detail == "read_model_age_limit"
    owner.observe(progress(), observed_at=102.0)
    assert owner.worker_health.state is WorkerState.READY


def test_failed_progress_preserves_metric_values_and_their_original_timestamp():
    owner = ReadModelHealth(7, clock=lambda: 100)
    owner.observe(
        progress(pending_partitions=1, oldest_head_age_seconds=12.5),
        observed_at=100,
    )
    timestamp = read_model_observed_timestamp._value.get()
    owner.unavailable()
    assert read_model_progress_available._value.get() == 0
    assert read_model_pending_partitions._value.get() == 1
    assert read_model_oldest_head_age._value.get() == 12.5
    assert read_model_observed_timestamp._value.get() == timestamp


@pytest.mark.parametrize(
    "value", [None, progress(generation=8), progress().model_copy(update={"slots": True})]
)
def test_wrong_generation_and_copied_malformed_progress_reject(value):
    owner = ReadModelHealth(7)
    with pytest.raises(AccountingProtocolUnavailable):
        owner.observe(value, observed_at=100.0)
    assert owner.progress is None


@pytest.mark.parametrize("state", list(WorkerState))
def test_mandatory_report_task_must_explicitly_be_ready(state):
    assert AccountingProcessors(Processing(), Processing(state)).worker_health.state is state
