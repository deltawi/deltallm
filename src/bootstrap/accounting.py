"""Construct accounting services under the existing runtime lifecycle."""

from __future__ import annotations

from prisma import Prisma

from src.accounting_settings import AccountingProtocolSettings
from src.billing.accounting_projection import (
    AccountingCompatibilityProjector,
    AccountingProjectionConfig,
    AccountingProjectionWorker,
)
from src.billing.accounting_service import AccountingProtocolService
from src.billing.ledger import SpendLedgerService
from src.billing.spend import SpendTrackingService
from src.config import GeneralSettings, Settings
from src.db.accounting_pool import AccountingPostgresClient
from src.db.accounting_projection import AccountingProjectionRepository
from src.db.accounting_protocol import AccountingProtocolRepository
from src.db.audit_ingestion import AuditIngestionRepository
from src.redis_runtime import startup_setting
from src.realtime.config import RealtimeSettings


def resolve_accounting_settings(
    general: GeneralSettings, settings: Settings
) -> AccountingProtocolSettings:
    defaults = AccountingProtocolSettings()
    config = AccountingProtocolSettings.model_validate(
        {
            field: startup_setting(general, settings, field, getattr(defaults, field))
            for field in AccountingProtocolSettings.model_fields
        }
    )
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
    if realtime.enabled:
        raise RuntimeError("Accounting v2 requires a shared Realtime billing adapter")
    if startup_setting(general, settings, "embeddings_batch_enabled", False):
        raise RuntimeError("Accounting v2 requires a shared batch billing adapter")


def start_accounting_protocol(
    config: AccountingProtocolSettings,
    *,
    client: AccountingPostgresClient | None,
    owner_id: str,
) -> AccountingProtocolService | None:
    if not config.accounting_protocol_enabled:
        return None
    if client is None:
        raise RuntimeError("accounting protocol requires the dedicated accounting database pool")
    statement_seconds = config.accounting_statement_timeout_ms / 1000.0
    service = AccountingProtocolService(
        AccountingProtocolRepository(
            client,
            statement_budget_seconds=statement_seconds,
            grants_enabled=config.accounting_grants_enabled,
            grantee_id=owner_id,
            grant_target_operations=config.accounting_grant_target_operations,
            grant_ttl_seconds=config.accounting_grant_ttl_seconds,
        ),
        generation=config.accounting_protocol_generation,
        max_batch_size=config.accounting_microbatch_max_size,
        dwell_seconds=config.accounting_microbatch_dwell_ms / 1000.0,
        max_pending_reservations=config.accounting_reservation_max_pending,
        max_pending_finalizations=config.accounting_finalization_max_pending,
        max_reservation_retained_bytes=config.accounting_reservation_max_pending_bytes,
        max_finalization_retained_bytes=config.accounting_finalization_max_pending_bytes,
        statement_budget_seconds=statement_seconds,
        reservation_ack_budget_seconds=config.accounting_reservation_ack_timeout_ms / 1000.0,
        finalization_ack_budget_seconds=config.accounting_finalization_ack_timeout_ms / 1000.0,
    )
    service.start()
    return service


async def start_accounting_projection(
    config: AccountingProtocolSettings,
    *,
    client: Prisma | None,
    owner_id: str,
    general: GeneralSettings,
    settings: Settings,
) -> AccountingProjectionWorker | None:
    if not config.accounting_projection_worker_enabled:
        return None
    if client is None:
        raise RuntimeError("accounting projection requires its worker database allocation")
    projection = AccountingProjectionConfig(
        generation=config.accounting_protocol_generation,
        worker_id=owner_id,
        batch_size=config.accounting_projection_batch_size,
        max_concurrent_partitions=config.accounting_projection_max_concurrent_partitions,
        lease_seconds=config.accounting_projection_lease_seconds,
        poll_interval_seconds=config.accounting_projection_poll_interval_ms / 1000.0,
        maintenance_interval_seconds=config.accounting_projection_maintenance_interval_ms / 1000.0,
        audit_max_pending_events=int(
            startup_setting(general, settings, "audit_ingestion_max_pending_events", 100_000)
        ),
        audit_required_reserve=int(
            startup_setting(general, settings, "audit_ingestion_required_reserve", 10_000)
        ),
        audit_max_attempts=int(
            startup_setting(general, settings, "audit_ingestion_max_attempts", 10)
        ),
    )
    worker = AccountingProjectionWorker(
        AccountingProjectionRepository(client),
        AccountingCompatibilityProjector(
            spend=SpendTrackingService(client, ledger=SpendLedgerService(client)),
            audit=AuditIngestionRepository(client),
            config=projection,
        ),
        projection,
    )
    await worker.start()
    return worker
