from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from src.config import Settings
from src.config_runtime.dynamic import DynamicConfigManager, DynamicConfigRestartRequiredError
from tests.test_output_policy_configuration import OutputConfigDB


@pytest.mark.parametrize(
    "field,environment,initial,candidate",
    [
        ("tier_policy_mode", "enforce", {}, "disabled"),
        ("tier_policy_mode", "disabled", {"tier_policy_mode": "shadow"}, "enforce"),
        ("tier_policy_mode", "disabled", {"tier_policy_mode": "enforce"}, "shadow"),
        ("tier_policy_missing_service_mode", "fail_closed", {}, "fail_open"),
        (
            "tier_policy_missing_service_mode",
            "fail_open",
            {"tier_policy_missing_service_mode": "fail_closed"},
            "fail_open",
        ),
    ],
)
async def test_effective_tier_mode_change_requires_restart_before_mutation(
    field, environment, initial, candidate
):
    db = OutputConfigDB({"general_settings": initial})
    before = deepcopy(db.config_value)
    manager = DynamicConfigManager(
        db, None, {}, poll_interval_seconds=0, runtime_settings=Settings(**{field: environment})
    )
    await manager.initialize()
    mutation = AsyncMock()
    subscriber = AsyncMock()
    manager.subscribe(subscriber)
    try:
        with pytest.raises(DynamicConfigRestartRequiredError, match=field):
            await manager.update_config(
                {"general_settings": {field: candidate}},
                updated_by="test",
                transaction_mutation=mutation,
            )
        assert db.config_value == before
        assert db.output_reads == 0
        assert manager.get_config_generation() == 1
        mutation.assert_not_awaited()
        subscriber.assert_not_awaited()
    finally:
        await manager.close()


@pytest.mark.parametrize(
    "field,value",
    [("tier_policy_mode", "disabled"), ("tier_policy_missing_service_mode", "fail_open")],
)
async def test_unchanged_effective_tier_default_can_be_saved(field, value):
    db = OutputConfigDB({})
    manager = DynamicConfigManager(
        db, None, {}, poll_interval_seconds=0, runtime_settings=Settings(**{field: value})
    )
    await manager.initialize()
    try:
        await manager.update_config({"general_settings": {field: value}}, updated_by="test")
        assert getattr(manager.get_app_config().general_settings, field) == value
        assert db.config_value["general_settings"][field] == value
        assert db.output_reads == 0
    finally:
        await manager.close()


async def test_initial_database_tier_modes_can_override_environment():
    db = OutputConfigDB(
        {
            "general_settings": {
                "tier_policy_mode": "enforce",
                "tier_policy_missing_service_mode": "fail_closed",
            }
        }
    )
    manager = DynamicConfigManager(
        db, None, {}, poll_interval_seconds=0, runtime_settings=Settings()
    )
    try:
        await manager.initialize()
        general = manager.get_app_config().general_settings
        assert general.tier_policy_mode == "enforce"
        assert general.tier_policy_missing_service_mode == "fail_closed"
        assert manager.get_config_generation() == 1
    finally:
        await manager.close()
