from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.admin.output_policy import validate_tier_output_write
from src.config import AppConfig, Settings
from src.config_runtime.dynamic import DynamicConfigManager, DynamicConfigValidationError
from src.services.output_policy_configuration import validate_output_policy_configuration
from src.services.tier_policy_service import TierPolicyService
from tests.config.test_dynamic import FakeDB, FakeRedis


class OutputConfigDB(FakeDB):
    def __init__(self, config_value):
        super().__init__(config_value)
        self.output_reads = 0

    async def query_raw(self, query, *params):
        if "AS tier_enabled" in query:
            self.output_reads += 1
            return [{"tier_enabled": True}]
        return await super().query_raw(query, *params)


@pytest.mark.parametrize("failure", ["tier", "redis", "jwt", "custom"])
async def test_startup_checks_environment_tier_enforcement(failure):
    settings = Settings(
        tier_policy_mode="enforce",
        tier_policy_missing_service_mode="fail_open" if failure == "tier" else "fail_closed",
    )
    config = AppConfig(
        general_settings={
            "enable_jwt_auth": failure == "jwt",
            "custom_auth": "custom.handler" if failure == "custom" else None,
        }
    )
    db = SimpleNamespace(query_raw=AsyncMock(return_value=[{"tier_enabled": True}]))
    with pytest.raises(ValueError):
        await validate_output_policy_configuration(
            db,
            config,
            redis_available=failure != "redis",
            degraded_mode="fail_closed",
            runtime_settings=settings,
        )


async def test_explicit_config_overrides_environment_tier_settings():
    settings = Settings(tier_policy_mode="enforce", tier_policy_missing_service_mode="fail_open")
    db = SimpleNamespace(query_raw=AsyncMock(return_value=[{"tier_enabled": True}]))
    for config in (
        AppConfig(general_settings={"tier_policy_mode": "disabled"}),
        AppConfig(general_settings={"tier_policy_missing_service_mode": "fail_closed"}),
    ):
        await validate_output_policy_configuration(
            db, config, redis_available=True, degraded_mode="fail_closed", runtime_settings=settings
        )


@pytest.mark.parametrize("source", ["environment", "runtime"])
def test_admin_checks_effective_tier_settings(source):
    config = AppConfig(
        general_settings={"tier_policy_mode": "disabled"} if source == "runtime" else {}
    )
    service = TierPolicyService(repository=None, mode="enforce", missing_service_mode="fail_open")
    state = SimpleNamespace(
        app_config=config,
        settings=Settings(tier_policy_mode="enforce", tier_policy_missing_service_mode="fail_open"),
        tier_policy_service=service if source == "runtime" else None,
        limit_counter=SimpleNamespace(redis=object(), degraded_mode="fail_closed"),
    )
    request = Request({"type": "http", "app": SimpleNamespace(state=state)})
    with pytest.raises(HTTPException) as error:
        validate_tier_output_write(request, {"output_tpm_limit": 10})
    assert error.value.status_code == 400
    assert "tier_policy_missing_service_mode" in error.value.detail
    validate_tier_output_write(request, {"output_tpm_limit": None})


@pytest.mark.parametrize("initial_mode", ["enforce", "disabled", "shadow", "environment"])
@pytest.mark.parametrize("related_mutation", [False, True])
async def test_tier_config_rejection_precedes_persistence_and_related_mutation(
    initial_mode, related_mutation
):
    general = {"redis_degraded_mode": "fail_closed"}
    if initial_mode != "environment":
        general.update(
            tier_policy_mode=initial_mode, tier_policy_missing_service_mode="fail_closed"
        )
    db = OutputConfigDB({"general_settings": general})
    before = deepcopy(db.config_value)
    redis = FakeRedis()
    manager = DynamicConfigManager(
        db,
        redis,
        {},
        poll_interval_seconds=None,
        runtime_settings=Settings(
            tier_policy_mode="enforce", tier_policy_missing_service_mode="fail_closed"
        ),
    )
    await manager.initialize()
    subscriber = AsyncMock()
    mutation = AsyncMock()
    manager.subscribe(subscriber)
    try:
        with pytest.raises(DynamicConfigValidationError, match="tier_policy_missing_service_mode"):
            await manager.update_config(
                {
                    "general_settings": {
                        "tier_policy_mode": "enforce",
                        "tier_policy_missing_service_mode": "fail_open",
                    }
                },
                updated_by="test",
                transaction_mutation=mutation if related_mutation else None,
            )
        assert db.config_value == before
        assert db.updated_by is None
        assert db.output_reads == 1
        assert manager.get_config_generation() == 1
        assert redis.messages == []
        subscriber.assert_not_awaited()
        mutation.assert_not_awaited()
    finally:
        await manager.close()


async def test_valid_tier_config_checks_policy_once_and_unrelated_config_does_not_read_it():
    db = OutputConfigDB(
        {
            "general_settings": {
                "tier_policy_mode": "shadow",
                "tier_policy_missing_service_mode": "fail_closed",
                "redis_degraded_mode": "fail_closed",
            }
        }
    )
    redis = FakeRedis()
    manager = DynamicConfigManager(db, redis, {}, poll_interval_seconds=None)
    await manager.initialize()
    try:
        await manager.update_config(
            {"general_settings": {"tier_policy_mode": "enforce"}}, updated_by="test"
        )
        assert manager.get_app_config().general_settings.tier_policy_mode == "enforce"
        assert db.config_value["general_settings"]["tier_policy_mode"] == "enforce"
        assert db.output_reads == 1
        await manager.update_config(
            {"general_settings": {"instance_name": "Changed"}}, updated_by="test"
        )
        assert manager.get_app_config().general_settings.instance_name == "Changed"
        assert db.output_reads == 1
    finally:
        await manager.close()


async def test_peer_reload_rejects_invalid_tier_config_and_preserves_runtime():
    db = OutputConfigDB(
        {
            "general_settings": {
                "tier_policy_mode": "enforce",
                "tier_policy_missing_service_mode": "fail_closed",
                "redis_degraded_mode": "fail_closed",
            }
        }
    )
    manager = DynamicConfigManager(db, FakeRedis(), {}, poll_interval_seconds=None)
    await manager.initialize()
    subscriber = AsyncMock()
    manager.subscribe(subscriber)
    before = manager.get_app_config()
    try:
        db.config_value["general_settings"]["tier_policy_missing_service_mode"] = "fail_open"
        assert not await manager._reload_config_from_source(source="poll")
        assert manager.get_app_config() == before
        assert manager.get_config_generation() == 1
        subscriber.assert_not_awaited()
        db.config_value["general_settings"].update(
            tier_policy_missing_service_mode="fail_closed", instance_name="Recovered"
        )
        assert await manager._reload_config_from_source(source="poll")
        assert manager.get_app_config().general_settings.instance_name == "Recovered"
        subscriber.assert_awaited_once()
    finally:
        await manager.close()
