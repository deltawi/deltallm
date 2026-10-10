"""API replicas retain one local issue graph; persistence batches use HTTP."""

from __future__ import annotations

from src.accounting_settings import AccountingProtocolSettings
from src.billing.accounting.health.accounting_admission_monitor import AccountingAdmissionMonitor
from src.billing.accounting.transport.accounting_http import AccountingHttpTransport
from src.billing.accounting.permits.accounting_local_cursors import LocalCursorStore
from src.billing.accounting.permits.accounting_local_issuer import LocalPermitIssuer
from src.billing.accounting.permits.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting.permits.accounting_local_returns import LocalReturnWorker
from src.billing.accounting.accounting_local_runtime import LocalAccountingRuntime
from src.billing.accounting.accounting_local_service import LocalAccountingService
from src.billing.accounting.journal.accounting_local_terminal import LocalTerminalOwner
from src.billing.accounting.transport.accounting_remote_leases import RemoteLocalLeasePersistence
from src.billing.accounting.journal.accounting_terminal_receipts import JournalReceipt
from src.process_lifecycle import ProcessLifecycle
from src.telemetry.lifecycle import WorkerHealth, WorkerState


class ApiAccountingRuntime:
    def __init__(
        self,
        local: LocalAccountingRuntime,
        monitor: AccountingAdmissionMonitor,
        lifecycle: ProcessLifecycle,
    ) -> None:
        self.local = local
        self.monitor = monitor
        self._lifecycle = lifecycle

    @property
    def worker_health(self) -> WorkerHealth:
        if self.monitor.worker_health.state is not WorkerState.READY:
            return self.monitor.worker_health
        return self.local.worker_health

    async def start(self, *, expires_at: float) -> None:
        await self.monitor.start(expires_at=expires_at)
        await self.local.start(expires_at=expires_at)
        self._lifecycle.register_claim_stop(self.local.service.issuer.stop_admission)

    async def close(self, *, expires_at: float) -> None:
        try:
            drained = await self.local.close(expires_at=expires_at)
        finally:
            stopped = await self.monitor.close(expires_at=expires_at)
        if not drained or not stopped:
            raise RuntimeError("API accounting local drain did not complete")


def build_api_accounting_runtime(
    transport: AccountingHttpTransport,
    config: AccountingProtocolSettings,
    lifecycle: ProcessLifecycle,
    *,
    owner_id: str,
    max_subjects: int = 1024,
) -> ApiAccountingRuntime:
    persistence = RemoteLocalLeasePersistence(
        transport,
        generation=config.accounting_protocol_generation,
        owner_id=owner_id,
    )
    monitor = AccountingAdmissionMonitor(persistence)
    cursors = LocalCursorStore(
        generation=config.accounting_protocol_generation,
        max_entries=max_subjects,
        max_retained_bytes=config.accounting_reservation_max_pending_bytes,
    )
    receipts = LocalReceiptStore(
        max_entries=config.accounting_finalization_max_pending,
        max_retained_bytes=config.accounting_finalization_max_pending_bytes,
    )
    issuer = LocalPermitIssuer(
        persistence,
        cursors,
        receipts,
        target_operations=config.accounting_grant_target_operations,
        admission_health=monitor,
    )
    service = LocalAccountingService(
        persistence,
        generation=config.accounting_protocol_generation,
        issuer=issuer,
        terminal=LocalTerminalOwner(
            persistence,
            receipts,
            generation=config.accounting_protocol_generation,
            receipt_type=JournalReceipt,
        ),
        max_batch_size=config.accounting_microbatch_max_size,
        dwell_seconds=config.accounting_microbatch_dwell_ms / 1000,
        max_pending_reservations=config.accounting_reservation_max_pending,
        max_pending_finalizations=config.accounting_finalization_max_pending,
        max_reservation_retained_bytes=config.accounting_reservation_max_pending_bytes,
        max_finalization_retained_bytes=config.accounting_finalization_max_pending_bytes,
        statement_budget_seconds=config.accounting_statement_timeout_ms / 1000,
        reservation_ack_budget_seconds=config.accounting_reservation_ack_timeout_ms / 1000,
        finalization_ack_budget_seconds=config.accounting_finalization_ack_timeout_ms / 1000,
    )
    return ApiAccountingRuntime(
        LocalAccountingRuntime(service, LocalReturnWorker(persistence, cursors, issuer)),
        monitor,
        lifecycle,
    )
