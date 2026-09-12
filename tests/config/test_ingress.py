from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.config import GeneralSettings, Settings
from src.config_runtime.dynamic import DynamicConfigManager, DynamicConfigRestartRequiredError
from src.ingress import IngressLimits

pytestmark = pytest.mark.hermetic


@pytest.mark.parametrize(
    ("name", "invalid"),
    [
        ("max_active", 0),
        ("max_waiters", -1),
        ("queue_timeout_ms", 0),
        ("max_body_bytes", 0),
        ("max_buffered_bytes", 0),
        ("body_timeout_seconds", 0),
        ("health_max_active", 0),
    ],
)
def test_ingress_settings_reject_invalid_bounds(name, invalid) -> None:
    value = {f"gateway_ingress_{name}": invalid}
    for model in (Settings, GeneralSettings):
        with pytest.raises(ValueError):
            model.model_validate(value)


def test_defaults_environment_explicit_settings_and_chart_agree(monkeypatch) -> None:
    defaults = IngressLimits()
    general, environment = GeneralSettings(), Settings()
    assert IngressLimits.from_settings(general, environment) == defaults
    chart = yaml.safe_load(Path("deploy/kubernetes/helm/values.yaml").read_text())
    example = yaml.safe_load(Path("config.example.yaml").read_text())
    for name in IngressLimits.__dataclass_fields__:
        field = f"gateway_ingress_{name}"
        assert getattr(general, field) == getattr(environment, field) == getattr(defaults, name)
        assert chart["config"]["general_settings"][field] == getattr(defaults, name)
        assert example["general_settings"][field] == getattr(defaults, name)
    monkeypatch.setenv("DELTALLM_GATEWAY_INGRESS_ENABLED", "true")
    monkeypatch.setenv("DELTALLM_GATEWAY_INGRESS_MAX_ACTIVE", "17")
    environment = Settings()
    assert IngressLimits.from_settings(general, environment).max_active == 17
    explicit = GeneralSettings(gateway_ingress_enabled=False, gateway_ingress_max_active=19)
    effective = IngressLimits.from_settings(explicit, environment)
    assert effective.max_active == 19
    assert effective.enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(IngressLimits.__dataclass_fields__))
async def test_ingress_changes_require_restart(name: str) -> None:
    manager = DynamicConfigManager(db_client=None, redis_client=None, file_config={})
    await manager.initialize()
    field = f"gateway_ingress_{name}"
    original = getattr(manager.get_app_config().general_settings, field)
    changed = not original if isinstance(original, bool) else original + 1
    with pytest.raises(DynamicConfigRestartRequiredError, match=field):
        await manager.update_config({"general_settings": {field: changed}}, updated_by="test")
    assert getattr(manager.get_app_config().general_settings, field) == original
    await manager.close()


@pytest.mark.asyncio
async def test_explicit_default_cannot_replace_environment_without_restart() -> None:
    manager = DynamicConfigManager(db_client=None, redis_client=None, file_config={})
    await manager.initialize()
    with pytest.raises(DynamicConfigRestartRequiredError, match="gateway_ingress_enabled"):
        await manager.update_config(
            {"general_settings": {"gateway_ingress_enabled": False}}, updated_by="test"
        )
    await manager.close()
