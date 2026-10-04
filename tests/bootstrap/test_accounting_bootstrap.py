from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.datastructures import State

from src.accounting_settings import AccountingProtocolSettings
from src.bootstrap.accounting import resolve_accounting_settings, start_accounting_protocol
from src.bootstrap.readiness import dependency_probes
from src.config import GeneralSettings, Settings
from src.realtime.config import RealtimeSettings


def test_accounting_settings_keep_the_shared_startup_precedence():
    settings = Settings(accounting_protocol_enabled=True, accounting_microbatch_max_size=16)
    config = resolve_accounting_settings(GeneralSettings(), settings)
    assert config.accounting_protocol_enabled is True
    assert config.accounting_microbatch_max_size == 16
    config = resolve_accounting_settings(
        GeneralSettings(accounting_protocol_enabled=False), settings
    )
    assert config.accounting_protocol_enabled is False


def test_disabled_accounting_does_not_create_a_service_or_require_a_pool():
    assert (
        start_accounting_protocol(AccountingProtocolSettings(), client=None, owner_id="test")
        is None
    )


def test_enabled_accounting_requires_its_named_database_owner():
    with pytest.raises(RuntimeError, match="dedicated accounting database pool"):
        start_accounting_protocol(
            AccountingProtocolSettings(accounting_protocol_enabled=True),
            client=None,
            owner_id="test",
        )


async def test_readiness_uses_the_accounting_pool_not_only_the_prisma_pool():
    service = MagicMock(readiness_probe=AsyncMock(return_value=False))
    state = State({"accounting_protocol_enabled": True, "accounting_protocol_service": service})
    probes = dependency_probes(state)
    assert await probes["accounting_database"]() is False
    service.readiness_probe.return_value = True
    assert await probes["accounting_database"]() is True
    assert service.readiness_probe.await_count == 2


async def test_missing_accounting_service_is_not_ready():
    probes = dependency_probes(State({"accounting_protocol_enabled": True}))
    assert await probes["accounting_database"]() is False


@pytest.mark.parametrize("feature", ["realtime", "batch"])
def test_v2_cannot_start_an_unmigrated_legacy_billing_writer(feature):
    general = GeneralSettings(
        accounting_protocol_enabled=True,
        realtime=RealtimeSettings(enabled=feature == "realtime"),
        embeddings_batch_enabled=feature == "batch",
    )
    with pytest.raises(
        RuntimeError,
        match=f"shared {feature if feature == 'batch' else 'Realtime'} billing adapter",
    ):
        resolve_accounting_settings(general, Settings())


def test_legacy_mode_preserves_realtime_and_batch_configuration():
    general = GeneralSettings(
        realtime=RealtimeSettings(enabled=True), embeddings_batch_enabled=True
    )
    assert resolve_accounting_settings(general, Settings()).accounting_protocol_enabled is False
