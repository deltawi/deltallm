"""Native HPA pressure includes both lanes and keeps unknown observations unknown."""

import pytest

from src.billing.accounting_projection_observation import NativeProjectionObservation
from src.billing.accounting_read_model_health import ReadModelHealth
from src.metrics.accounting_read_models import (
    native_oldest_work_age,
    native_work_observation_available,
    native_work_observed_timestamp,
)
from tests.test_accounting_health import Clock, Persistence, deadline, probe, snapshot
from tests.test_accounting_read_model_health import progress


class Presence:
    generation = 7

    def __init__(self):
        self.calls = []

    async def observe(self, *, expires_at):
        self.calls.append(expires_at)


async def owners(terminal_age, report_age):
    clock = Clock()
    terminal = probe(
        Persistence(
            snapshot(
                pending_entries=int(terminal_age is not None),
                outstanding_operations=int(terminal_age is not None),
                pending_bytes=8 * int(terminal_age is not None),
                oldest_age_seconds=terminal_age,
            )
        ),
        clock,
    )
    await terminal.refresh(expires_at=deadline())
    reporting = ReadModelHealth(7, clock=clock)
    reporting.observe(
        progress(
            pending_partitions=int(report_age is not None), oldest_head_age_seconds=report_age
        ),
        observed_at=clock.value,
    )
    presence = Presence()
    return (
        NativeProjectionObservation(presence, terminal, reporting),
        terminal,
        reporting,
        clock,
        presence,
    )


@pytest.mark.parametrize(
    "terminal,report,expected", [(None, None, 0), (12, 5, 12), (3, 14, 14), (70, 5, 70)]
)
async def test_native_scaling_observes_oldest_lane_without_additional_io(
    terminal, report, expected
):
    owner, _, _, _, presence = await owners(terminal, report)
    end = deadline()
    await owner.observe(expires_at=end)
    assert presence.calls == [end]
    assert native_work_observation_available._value.get() == 1
    assert native_oldest_work_age._value.get() == expected


@pytest.mark.parametrize("failure", ["terminal_stale", "report_stale", "missing", "unavailable"])
async def test_failed_observation_retains_last_value_and_original_timestamp(failure):
    owner, terminal, reporting, clock, _ = await owners(12, 5)
    await owner.observe(expires_at=deadline())
    timestamp = native_work_observed_timestamp._value.get()
    if failure in {"terminal_stale", "report_stale"}:
        clock.value += 5
    elif failure == "missing":
        reporting.observe(progress(slots=3), observed_at=clock.value)
    else:
        reporting.unavailable()
    await owner.observe(expires_at=deadline())
    assert native_work_observation_available._value.get() == 0
    assert native_oldest_work_age._value.get() == 12
    assert native_work_observed_timestamp._value.get() == timestamp
    assert terminal.retained_snapshot_bytes > 0


def test_mismatched_native_metric_generation_is_rejected():
    with pytest.raises(ValueError):
        NativeProjectionObservation(Presence(), probe(Persistence(), Clock()), ReadModelHealth(8))
