import pytest
import yaml

from src.request_work_settings import RequestWorkSettings
from tests.helm.test_ingress_settings import CHART, render

pytestmark = pytest.mark.helm


@pytest.mark.parametrize("field", RequestWorkSettings.model_fields)
def test_chart_rejects_invalid_work_bound(field):
    result = render("--set", f"config.general_settings.{field}=0")
    assert result.returncode != 0
    assert field in result.stderr


@pytest.mark.parametrize("overlay", [None, "values-eval.yaml", "values-production.yaml"])
def test_profiles_render_explicit_work_budgets(overlay):
    result = render(*(["-f", str(CHART / overlay)] if overlay else []))
    assert result.returncode == 0, result.stderr
    configs = [
        item
        for item in yaml.safe_load_all(result.stdout)
        if item and item.get("kind") == "ConfigMap"
    ]
    config = next(
        yaml.safe_load(item["data"]["config.yaml"])
        for item in configs
        if "config.yaml" in item.get("data", {})
    )
    for field, value in RequestWorkSettings().model_dump().items():
        assert config["general_settings"][field] == value
