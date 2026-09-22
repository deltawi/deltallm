from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.config import GeneralSettings, Settings
from src.lifecycle_settings import LifecycleSettings, resolve_lifecycle_settings
from src.process_lifecycle import ProcessLifecycle, ProcessState


def test_first_signal_fixes_every_deadline_and_closes_claim_admission():
    now = [100.0]
    lifecycle = ProcessLifecycle(LifecycleSettings(), clock=lambda: now[0])
    stopped = []
    lifecycle.register_claim_stop(lambda: stopped.append("batch"))
    lifecycle.mark_serving()
    deadlines = lifecycle.begin_drain()
    assert not lifecycle.ready
    assert deadlines.withdrawal == 105
    assert deadlines.responses == 150
    assert deadlines.cancellation == 155
    assert deadlines.workers == 175
    assert deadlines.close == deadlines.total == 180
    now[0] = 160
    lifecycle.register_claim_stop(lambda: stopped.append("late_worker"))
    assert lifecycle.begin_drain() is deadlines
    lifecycle.mark_serving()
    assert lifecycle.state == ProcessState.DRAINING
    assert stopped == ["batch", "late_worker"]
    lifecycle.mark_stopped()
    assert lifecycle.state == ProcessState.STOPPED
    assert lifecycle.begin_stopping() is deadlines
    assert lifecycle.state == ProcessState.STOPPED


def test_partial_startup_cannot_reopen_after_drain_and_attempts_all_claim_stops():
    lifecycle = ProcessLifecycle(LifecycleSettings())
    stopped = []

    def fail():
        raise RuntimeError("private credentials")

    lifecycle.register_claim_stop(fail)
    lifecycle.register_claim_stop(lambda: stopped.append(True))
    lifecycle.begin_stopping()
    lifecycle.mark_serving()
    assert lifecycle.state == ProcessState.STOPPING
    assert stopped == [True]


@pytest.mark.parametrize("value", [0, -1, 79, float("inf"), float("nan")])
def test_invalid_shutdown_budgets_fail_configuration(value):
    with pytest.raises(ValidationError):
        LifecycleSettings(lifecycle_shutdown_seconds=value)


def test_explicit_file_values_win_and_unspecified_fields_use_environment(monkeypatch):
    monkeypatch.setenv("DELTALLM_LIFECYCLE_WITHDRAWAL_SECONDS", "4")
    monkeypatch.setenv("DELTALLM_READINESS_CACHE_SECONDS", "2")
    environment = Settings()
    file = GeneralSettings(lifecycle_withdrawal_seconds=3)
    resolved = resolve_lifecycle_settings(file, environment)
    assert resolved.lifecycle_withdrawal_seconds == 3
    assert resolved.readiness_cache_seconds == 2


def test_reload_rejects_lifecycle_changes_including_field_presence():
    from src.config import AppConfig
    from src.config_runtime.dynamic import (
        DynamicConfigManager,
        DynamicConfigRestartRequiredError,
    )

    manager = SimpleNamespace(_config=AppConfig())
    candidate = AppConfig(general_settings=GeneralSettings(lifecycle_shutdown_seconds=80))
    with pytest.raises(DynamicConfigRestartRequiredError, match="lifecycle_shutdown_seconds"):
        DynamicConfigManager._reject_startup_only_changes(manager, candidate)
