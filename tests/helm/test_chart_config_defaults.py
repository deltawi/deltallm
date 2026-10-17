import json
from pathlib import Path

import pytest
import yaml

from src.config import GeneralSettings

pytestmark = pytest.mark.helm
CHART = Path(__file__).resolve().parents[2] / "deploy/kubernetes/helm"


def _chart_defaults() -> dict:
    return yaml.safe_load((CHART / "files/config-defaults.yaml").read_text())["general_settings"]


def test_chart_defaults_equal_gateway_defaults():
    code = json.loads(GeneralSettings().model_dump_json())
    mismatched = {
        key: (value, code.get(key, "<unknown setting>"))
        for key, value in _chart_defaults().items()
        if code.get(key, object()) != value
    }
    assert mismatched == {}


def test_values_file_does_not_repeat_chart_defaults():
    values = yaml.safe_load((CHART / "values.yaml").read_text())["config"]["general_settings"]
    assert set(values) & set(_chart_defaults()) == set()
