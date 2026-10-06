"""Resolve the single accounting startup contract without importing writers."""

from src.accounting_settings import AccountingProtocolSettings
from src.config import GeneralSettings, Settings
from src.redis_runtime import startup_setting
from src.realtime.config import RealtimeSettings


def read_accounting_settings(
    general: GeneralSettings, settings: Settings
) -> AccountingProtocolSettings:
    defaults = AccountingProtocolSettings()
    return AccountingProtocolSettings.model_validate(
        {
            field: startup_setting(general, settings, field, getattr(defaults, field))
            for field in AccountingProtocolSettings.model_fields
        }
    )


def resolve_accounting_settings(
    general: GeneralSettings, settings: Settings
) -> AccountingProtocolSettings:
    config = read_accounting_settings(general, settings)
    validate_legacy_accounting_writers(config, general=general, settings=settings)
    return config


def validate_legacy_accounting_writers(
    config: AccountingProtocolSettings, *, general: GeneralSettings, settings: Settings
) -> None:
    if not config.accounting_protocol_enabled:
        return
    realtime = startup_setting(general, settings, "realtime", RealtimeSettings())
    if not isinstance(realtime, RealtimeSettings):
        raise RuntimeError("Accounting requires validated Realtime settings")
