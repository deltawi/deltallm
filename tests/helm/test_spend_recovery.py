import pytest

from tests.helm.test_batch_worker_split import HELM_CHART_DIR, _render, _render_error
from tests.helm.test_dependency_capacity import capacity

pytestmark = pytest.mark.helm


@pytest.mark.parametrize(
    "override",
    [
        "config.general_settings.spend_ingestion_mode=legacy",
        "config.general_settings.spend_ingestion_worker_enabled=false",
        "config.general_settings.telemetry_db_pool_size=1",
        "config.general_settings.spend_settlement_db_pool_size=5",
    ],
)
def test_invalid_spend_recovery_allocation_is_rejected(override):
    error = _render_error("-f", str(HELM_CHART_DIR / "values-production.yaml"), "--set", override)
    assert "Spend operation intents require" in error


def test_reserved_pool_does_not_increase_peak_connection_budget():
    # Compare optional recovery allocations in a development profile; production
    # now requires intents. Settlement stays inside the same five DB connections.
    before = capacity(
        _render(
            "-f",
            str(HELM_CHART_DIR / "values-production.yaml"),
            "--set",
            "config.general_settings.spend_operation_intents_enabled=false",
            "--set",
            "managedLifecycle.production=false",
        )
    )
    after = capacity(_render("-f", str(HELM_CHART_DIR / "values-production.yaml")))
    assert {k: v for k, v in before.items() if k != "report.json"} == {
        k: v for k, v in after.items() if k != "report.json"
    }
