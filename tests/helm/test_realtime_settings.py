import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from src.realtime.config import RealtimeSettings

pytestmark = pytest.mark.helm
ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy/kubernetes/helm"
HELM = shutil.which("helm")


def test_realtime_values_and_schema_match_runtime_defaults():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    assert values["config"]["general_settings"]["realtime"] == RealtimeSettings().model_dump()
    schema = json.loads((CHART / "values.schema.json").read_text())
    realtime = schema["properties"]["config"]["properties"]["general_settings"]["properties"][
        "realtime"
    ]
    assert set(realtime["properties"]) == set(RealtimeSettings.model_fields)
    assert realtime["additionalProperties"] is False


@pytest.mark.skipif(HELM is None, reason="helm is not installed")
@pytest.mark.parametrize("grace,success", [(30, False), (45, True)])
def test_realtime_and_accounting_drain_fit_pod_grace(grace, success):
    command = [
        HELM,
        "template",
        "deltallm",
        str(CHART),
        "--set",
        "secret.values.masterKey=sk-testmasterkey1234567890A1",
        "--set",
        "secret.values.saltKey=test-salt-key-1234567890",
        "--set",
        "config.general_settings.realtime.enabled=true",
        "--set",
        "config.general_settings.spend_ingestion_mode=outbox",
        "--set",
        f"terminationGracePeriodSeconds={grace}",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert (result.returncode == 0) is success, result.stderr
    if not success:
        assert "Realtime cleanup_seconds + write_seconds" in result.stderr
