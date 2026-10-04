from pathlib import Path

import pytest
import yaml

from src.config import GeneralSettings, Settings
from src.config_runtime.dynamic import DynamicConfigManager, DynamicConfigRestartRequiredError
from src.request_work_settings import RequestWorkSettings, resolve_request_work_settings
from src.route_group_config import RouterSettings

pytestmark = pytest.mark.hermetic


@pytest.mark.parametrize("field", RequestWorkSettings.model_fields)
def test_request_work_bounds_environment_and_examples(field, monkeypatch):
    default = getattr(RequestWorkSettings(), field)
    for model in (GeneralSettings, Settings):
        assert getattr(model(), field) == default
        with pytest.raises(ValueError):
            model.model_validate({field: 0})
    for file, parts in [
        ("config.example.yaml", ("general_settings",)),
        ("deploy/kubernetes/helm/values.yaml", ("config", "general_settings")),
        ("deploy/kubernetes/helm/values-production.yaml", ("config", "general_settings")),
    ]:
        values = yaml.safe_load(Path(file).read_text())
        for part in parts:
            values = values[part]
        assert values[field] == default
    monkeypatch.setenv("DELTALLM_" + field.upper(), str(default + 1))
    assert (
        getattr(resolve_request_work_settings(GeneralSettings(), Settings()), field) == default + 1
    )
    assert (
        getattr(
            resolve_request_work_settings(GeneralSettings(**{field: default}), Settings()), field
        )
        == default
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", RequestWorkSettings.model_fields)
@pytest.mark.parametrize("explicit_default", [False, True])
async def test_request_work_changes_require_restart_before_persistence(field, explicit_default):
    manager = DynamicConfigManager(db_client=None, redis_client=None, file_config={})
    await manager.initialize()
    original = getattr(manager.get_app_config().general_settings, field)
    try:
        with pytest.raises(DynamicConfigRestartRequiredError, match=field):
            await manager.update_config(
                {"general_settings": {field: original if explicit_default else original + 1}},
                updated_by="test",
            )
        assert getattr(manager.get_app_config().general_settings, field) == original
    finally:
        await manager.close()


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_total_router_timeout_is_positive_and_finite(value):
    with pytest.raises(ValueError):
        RouterSettings(timeout=value)
