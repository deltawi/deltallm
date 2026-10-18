import pytest

from src.config import GeneralSettings, Settings
from src.runtime_settings import resolve_redis_degraded_mode

pytestmark = pytest.mark.hermetic


def test_environment_applies_when_config_omits_redis_degraded_mode(monkeypatch):
    monkeypatch.setenv("DELTALLM_REDIS_DEGRADED_MODE", "fail_closed")
    assert resolve_redis_degraded_mode(GeneralSettings(), Settings()) == "fail_closed"


def test_explicit_config_wins_over_environment(monkeypatch):
    monkeypatch.setenv("DELTALLM_REDIS_DEGRADED_MODE", "fail_closed")
    general = GeneralSettings(redis_degraded_mode="fail_open")
    assert resolve_redis_degraded_mode(general, Settings()) == "fail_open"


def test_default_is_fail_open(monkeypatch):
    monkeypatch.delenv("DELTALLM_REDIS_DEGRADED_MODE", raising=False)
    assert resolve_redis_degraded_mode(GeneralSettings(), Settings()) == "fail_open"
