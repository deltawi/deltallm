from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.config import AppConfig, Settings
from src.config_runtime.dynamic import DynamicConfigManager, DynamicConfigValidationError
from src.db.tiers import TierModelPolicyRecord, TierRepository
from src.services.output_policy_configuration import validate_output_policy_configuration
from tests.conftest import FakeRedis
from tests.db.tier_migration_helpers import cleanup, connect_prisma, seed_tier, seed_tier_version

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("initial_mode", ["disabled", "enforce"])
async def test_active_tier_output_guards_startup_and_config_transaction(monkeypatch, initial_mode):
    db = await connect_prisma()
    identity = f"output-config-{uuid4().hex}"
    monkeypatch.setattr("src.config_runtime.dynamic._CONFIG_ROW_NAME", identity)
    settings = Settings(
        tier_policy_mode=initial_mode, tier_policy_missing_service_mode="fail_closed"
    )
    manager = DynamicConfigManager(
        db, FakeRedis(), {}, poll_interval_seconds=0, runtime_settings=settings
    )
    try:
        await seed_tier(db, tier_id=identity, tier_key=identity)
        await seed_tier_version(
            db, tier_id=identity, tier_version_id=identity, version_number=1, status="draft"
        )
        repository = TierRepository(db)
        await repository.create_model_policy(
            tier_id=identity,
            tier_version_id=identity,
            expected_revision=0,
            policy=TierModelPolicyRecord("", identity, "m", output_tpm_limit=10),
        )
        await repository.activate_tier_version(
            tier_id=identity,
            tier_version_id=identity,
            expected_revision=1,
            expected_active_version_id=None,
        )
        with pytest.raises(ValueError, match="tier_policy_missing_service_mode"):
            await validate_output_policy_configuration(
                db,
                AppConfig(),
                redis_available=True,
                degraded_mode="fail_closed",
                runtime_settings=Settings(tier_policy_mode="enforce"),
            )
        await manager.initialize()
        mutation = AsyncMock()
        with pytest.raises(DynamicConfigValidationError, match="tier_policy_missing_service_mode"):
            await manager.update_config(
                {
                    "general_settings": {
                        "tier_policy_mode": "enforce",
                        "tier_policy_missing_service_mode": "fail_open",
                        "redis_degraded_mode": "fail_closed",
                    }
                },
                updated_by=identity,
                transaction_mutation=mutation,
            )
        mutation.assert_not_awaited()
        assert (
            await db.query_raw(
                "SELECT config_value FROM deltallm_config WHERE config_name = $1", identity
            )
            == []
        )
        assert manager.get_config_generation() == 1
    finally:
        await manager.close()
        await db.execute_raw("DELETE FROM deltallm_config WHERE config_name = $1", identity)
        await cleanup(db, tier_ids=(identity,))
        await db.disconnect()
