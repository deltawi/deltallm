"""Build native accounting role graphs with one pool and explicit ownership."""

from __future__ import annotations

from src.accounting_settings import AccountingProtocolSettings
from src.billing.accounting_admission_monitor import AccountingAdmissionMonitor
from src.billing.accounting_health import AccountingBacklogPolicy, AccountingBacklogProbe
from src.billing.accounting_journal_runtime import JournalProcessingWorker, JournalWorkerConfig
from src.billing.accounting_lane_group import AccountingLaneGroup
from src.billing.accounting_read_model_health import ReadModelHealth
from src.billing.accounting_native_observation import NativeAccountingObservation
from src.billing.accounting_presence import ProjectionPresencePublisher
from src.billing.accounting_projection_observation import NativeProjectionObservation
from src.billing.accounting_read_model_claims import ReadModelWorkerConfig
from src.billing.accounting_read_model_runtime import ReadModelProcessingWorker
from src.billing.accounting_recovery import AccountingRecoveryWorker, RecoveryConfig
from src.billing.accounting_rpc_service import AccountingRpcService
from src.bootstrap.accounting_roles import (
    AccountingProcessors,
    AccountingProjectionRuntime,
    AccountingRequestRuntime,
)
from src.db.accounting_calls import AccountingQueryClient
from src.db.accounting_health import AccountingBacklogRepository
from src.db.accounting_journal_worker import AccountingJournalWorkerRepository
from src.db.accounting_presence import AccountingPresenceRepository
from src.db.accounting_read_model import AccountingReadModelRepository
from src.db.accounting_recovery import AccountingRecoveryRepository
from src.process_lifecycle import ProcessLifecycle


def backlog_probe(
    client: AccountingQueryClient,
    config: AccountingProtocolSettings,
) -> AccountingBacklogProbe:
    return AccountingBacklogProbe(
        AccountingBacklogRepository(
            client,
            statement_budget_seconds=config.accounting_statement_timeout_ms / 1000,
        ),
        AccountingBacklogPolicy(generation=config.accounting_protocol_generation),
    )


def build_accounting_request_runtime(
    client: AccountingQueryClient,
    config: AccountingProtocolSettings,
    lifecycle: ProcessLifecycle,
) -> AccountingRequestRuntime:
    probe = backlog_probe(client, config)
    monitor = AccountingAdmissionMonitor(
        NativeAccountingObservation(
            probe,
            AccountingPresenceRepository(client),
            generation=config.accounting_protocol_generation,
        )
    )
    service = AccountingRpcService(
        client,
        generation=config.accounting_protocol_generation,
        probe=probe,
        projection_health=monitor,
        statement_seconds=config.accounting_statement_timeout_ms / 1000,
        grant_ttl_seconds=config.accounting_grant_ttl_seconds,
        max_batch_size=config.accounting_microbatch_max_size,
        dwell_seconds=config.accounting_microbatch_dwell_ms / 1000,
        max_pending=config.accounting_finalization_max_pending,
        max_retained_bytes=config.accounting_finalization_max_pending_bytes,
        terminal_ack_seconds=config.accounting_finalization_ack_timeout_ms / 1000,
    )
    return AccountingRequestRuntime(service, monitor, lifecycle)


def build_accounting_projection_runtime(
    client: AccountingQueryClient,
    config: AccountingProtocolSettings,
    lifecycle: ProcessLifecycle,
    *,
    owner_id: str,
) -> AccountingProjectionRuntime:
    if config.accounting_execution_mode == "local_journal":
        processing, read_models, reporting_health = native_processing_lanes(
            client, config, owner_id=owner_id
        )
    else:
        processing = JournalProcessingWorker(
            AccountingJournalWorkerRepository(
                client,
                statement_budget_seconds=config.accounting_statement_timeout_ms / 1000,
            ),
            JournalWorkerConfig(
                generation=config.accounting_protocol_generation,
                worker_id=owner_id,
                batch_size=config.accounting_projection_batch_size,
                lease_seconds=config.accounting_projection_lease_seconds,
                poll_seconds=config.accounting_projection_poll_interval_ms / 1000,
            ),
        )
        read_models = ReadModelProcessingWorker(
            AccountingReadModelRepository(
                client,
                statement_budget_seconds=config.accounting_statement_timeout_ms / 1000,
            ),
            ReadModelWorkerConfig(
                generation=config.accounting_protocol_generation,
                worker_id=owner_id,
                batch_size=config.accounting_projection_batch_size,
                lease_seconds=config.accounting_projection_lease_seconds,
                poll_seconds=config.accounting_projection_poll_interval_ms / 1000,
            ),
        )
        reporting_health = read_models.progress_health
    presence = ProjectionPresencePublisher(
        AccountingPresenceRepository(client),
        generation=config.accounting_protocol_generation,
        processing=AccountingProcessors(processing, read_models),
    )
    probe = backlog_probe(client, config)
    recovery = AccountingRecoveryWorker(
        AccountingRecoveryRepository(
            client,
            statement_budget_seconds=config.accounting_statement_timeout_ms / 1000,
        ),
        probe,
        RecoveryConfig(
            generation=config.accounting_protocol_generation,
            batch_size=config.accounting_projection_batch_size,
            poll_seconds=config.accounting_projection_maintenance_interval_ms / 1000,
        ),
        observer=NativeProjectionObservation(presence, probe, reporting_health),
    )
    return AccountingProjectionRuntime(processing, read_models, recovery, presence, lifecycle)


def native_processing_lanes(
    client: AccountingQueryClient,
    config: AccountingProtocolSettings,
    *,
    owner_id: str,
) -> tuple[AccountingLaneGroup, AccountingLaneGroup, ReadModelHealth]:
    reporting_count = config.accounting_projection_max_concurrent_partitions
    if (
        not 1 <= reporting_count <= 4
        or config.accounting_hot_path_db_pool_size < reporting_count + 4
    ):
        raise ValueError("Native lanes require two reserved database connections")
    common = dict(
        generation=config.accounting_protocol_generation,
        batch_size=config.accounting_projection_batch_size,
        lease_seconds=config.accounting_projection_lease_seconds,
        poll_seconds=config.accounting_projection_poll_interval_ms / 1000,
    )
    health = ReadModelHealth(config.accounting_protocol_generation)
    processing = AccountingLaneGroup(
        tuple(
            JournalProcessingWorker(
                AccountingJournalWorkerRepository(
                    client, statement_budget_seconds=config.accounting_statement_timeout_ms / 1000
                ),
                JournalWorkerConfig(worker_id=f"{owner_id}:terminal:{lane}", **common),
            )
            for lane in range(2)
        )
    )
    reports = AccountingLaneGroup(
        tuple(
            ReadModelProcessingWorker(
                AccountingReadModelRepository(
                    client, statement_budget_seconds=config.accounting_statement_timeout_ms / 1000
                ),
                ReadModelWorkerConfig(worker_id=f"{owner_id}:report:{lane}", **common),
                progress_health=health,
                observe_progress=lane == 0,
            )
            for lane in range(reporting_count)
        )
    )
    return processing, reports, health
