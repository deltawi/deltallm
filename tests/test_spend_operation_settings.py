import pytest

from src.config import GeneralSettings, Settings
from src.spend_operation_settings import SpendOperationAllocation


def test_settlement_is_carved_from_existing_budget():
    general = GeneralSettings(
        spend_operation_intents_enabled=True,
        spend_ingestion_mode="outbox",
        spend_settlement_db_pool_size=2,
    )
    resolved = SpendOperationAllocation.resolve(general, Settings(), telemetry_connections=5)
    assert resolved.enabled and resolved.settlement_connections == 2
    assert (5 - resolved.settlement_connections) + resolved.settlement_connections == 5


@pytest.mark.parametrize(
    "overrides,pool",
    [
        ({"spend_ingestion_mode": "legacy"}, 5),
        ({"spend_ingestion_worker_enabled": False}, 5),
        ({}, 1),
        ({"spend_settlement_db_pool_size": 5}, 5),
    ],
)
def test_incompatible_cutover_fails_closed(overrides, pool):
    with pytest.raises(ValueError, match="require outbox"):
        general = GeneralSettings(
            **{
                "spend_operation_intents_enabled": True,
                "spend_ingestion_mode": "outbox",
                "telemetry_db_pool_size": pool,
                **overrides,
            }
        )
        SpendOperationAllocation.resolve(general, Settings(), telemetry_connections=pool)


def test_disabled_mode_does_not_allocate_a_settlement_pool():
    resolved = SpendOperationAllocation.resolve(
        GeneralSettings(), Settings(), telemetry_connections=0
    )
    assert not resolved.enabled and resolved.settlement_connections == 0


def test_environment_cutover_is_validated_but_explicit_file_setting_wins(monkeypatch):
    monkeypatch.setenv("DELTALLM_SPEND_OPERATION_INTENTS_ENABLED", "true")
    settings = Settings(spend_ingestion_mode="outbox")
    assert SpendOperationAllocation.resolve(
        GeneralSettings(), settings, telemetry_connections=5
    ).enabled
    assert not SpendOperationAllocation.resolve(
        GeneralSettings(spend_operation_intents_enabled=False), settings, telemetry_connections=5
    ).enabled


def test_cutover_validates_after_resolving_file_and_environment_sources():
    general = GeneralSettings(spend_operation_intents_enabled=True)
    environment = Settings(spend_ingestion_mode="outbox", spend_settlement_db_pool_size=2)
    resolved = SpendOperationAllocation.resolve(general, environment, telemetry_connections=5)
    assert resolved.enabled and resolved.settlement_connections == 2
