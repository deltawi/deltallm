import pytest
from pydantic import ValidationError

from src.config import AppConfig, ModelInfo, Settings
from src.realtime.config import RealtimeSettings


def test_realtime_is_opt_in_and_environment_object_is_typed(monkeypatch):
    assert not AppConfig().general_settings.realtime.enabled
    monkeypatch.setenv("DELTALLM_REALTIME", '{"enabled":true,"session_seconds":120}')
    settings = Settings(_env_file=None)
    assert settings.realtime.enabled
    assert settings.realtime.transport_limits().session_seconds == 120


@pytest.mark.parametrize(
    "change",
    [
        {"session_seconds": 3601},
        {"max_turns": 1001},
        {"max_output_tokens": 4097},
        {"max_connections": 0},
        {"handshake_seconds": float("nan")},
        {"idle_seconds": 1, "health_seconds": 5},
        {"max_input_bytes": 1024},
        {"unknown_setting": True},
    ],
)
def test_invalid_realtime_bounds_rejected(change):
    with pytest.raises((ValidationError, ValueError)):
        RealtimeSettings(**change)


def test_cached_audio_price_keeps_exact_decimal_string():
    info = ModelInfo(input_cost_per_audio_token_cache_hit="0.000000000000000123")
    assert info.input_cost_per_audio_token_cache_hit == "1.23E-16"
    with pytest.raises(ValidationError):
        ModelInfo(input_cost_per_audio_token_cache_hit=False)


def test_conversations_cannot_declare_duration_usage():
    with pytest.raises(ValidationError):
        ModelInfo(mode="realtime", realtime_usage_type="duration")
