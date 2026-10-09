from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import State

from src.billing.charges.realtime_native import NativeRealtimeBilling
from src.billing.spend.spend import SpendTrackingService
from src.billing.spend.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.bootstrap.realtime import init_realtime_runtime
from src.config import AppConfig, GeneralSettings, Settings
from src.realtime.config import RealtimeSettings
from src.telemetry.lifecycle import WorkerState
from tests.realtime.test_native_billing import runtime as _native_runtime

native_runtime = _native_runtime


@pytest.mark.parametrize("enabled", [False, True])
async def test_native_realtime_uses_shared_health_without_legacy_recovery(native_runtime, enabled):
    _, service, _, _ = native_runtime
    db = SimpleNamespace(query_raw=AsyncMock(side_effect=AssertionError("legacy recovery lookup")))
    spend = SpendIngestionService(
        db_client=db,
        writer=SpendTrackingService(db),
        accounting=service,
        config=SpendIngestionConfig(enabled=True, worker_enabled=False),
    )
    state = State()
    state.settings = Settings()
    state.redis = AsyncMock()
    state.spend_tracking_service = spend
    state.routing_runtime_generation_store = None
    state.callable_target_grant_service = None
    state.tier_policy_service = SimpleNamespace(mode="disabled")
    state.key_service = None
    cfg = AppConfig(
        general_settings=GeneralSettings(
            realtime=RealtimeSettings(enabled=enabled),
            accounting_protocol_enabled=True,
        )
    )
    result = await init_realtime_runtime(state, cfg)
    assert spend.worker_health.state is WorkerState.DISABLED
    assert spend.realtime_recovery is None
    db.query_raw.assert_not_awaited()
    if enabled:
        assert result.ready
        assert isinstance(result.admission.billing, NativeRealtimeBilling)
        await service.close()
        assert not result.ready
    else:
        assert result is None
