from __future__ import annotations

from collections.abc import Awaitable, Callable

from src.batch.models import BatchWebhookOutboxRecord
from src.batch.repository import BatchRepository
from src.batch.webhooks.audit import persist_batch_webhook_terminal_audit
from src.db.repositories import AuditRepository
from src.services.audit_service import AuditService


class BatchWebhookTerminalRecorder:
    """Commit a terminal webhook transition and its audit row atomically."""

    def __init__(
        self,
        *,
        repository: BatchRepository,
        audit_service: AuditService | None,
        worker_id: str,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.worker_id = worker_id

    async def mark_delivered(
        self,
        record: BatchWebhookOutboxRecord,
        *,
        status_code: int,
    ) -> bool:
        return await self._mark_terminal(
            record,
            outcome="delivered",
            reason="delivered",
            status_code=status_code,
            update=lambda repository: repository.mark_webhook_outbox_delivered(
                event_id=record.event_id,
                worker_id=self.worker_id,
                attempt_count=record.attempt_count,
                status_code=status_code,
            ),
        )

    async def mark_failed(
        self,
        record: BatchWebhookOutboxRecord,
        *,
        reason: str,
        status_code: int | None,
    ) -> bool:
        return await self._mark_terminal(
            record,
            outcome="failed",
            reason=reason,
            status_code=status_code,
            update=lambda repository: repository.mark_webhook_outbox_failed(
                event_id=record.event_id,
                worker_id=self.worker_id,
                attempt_count=record.attempt_count,
                status_code=status_code,
                error=reason,
            ),
        )

    async def fail_exhausted_leases(self, *, limit: int) -> list[BatchWebhookOutboxRecord]:
        if self.audit_service is None:
            return await self.repository.fail_exhausted_webhook_outbox_leases(limit=limit)

        transaction = self._transaction()
        async with transaction() as tx:
            repository = self.repository.with_prisma(tx)
            failed = await repository.fail_exhausted_webhook_outbox_leases(limit=limit)
            audit_repository = AuditRepository(tx)
            for record in failed:
                reason = record.last_error or "max_attempts_exhausted_after_lease_expiry"
                await persist_batch_webhook_terminal_audit(
                    audit_service=self.audit_service,
                    audit_repository=audit_repository,
                    repository=repository,
                    record=record,
                    worker_id=self.worker_id,
                    outcome="failed",
                    reason=reason,
                    status_code=record.last_status_code,
                )
            return failed

    async def _mark_terminal(
        self,
        record: BatchWebhookOutboxRecord,
        *,
        outcome: str,
        reason: str,
        status_code: int | None,
        update: Callable[[BatchRepository], Awaitable[bool]],
    ) -> bool:
        if self.audit_service is None:
            return await update(self.repository)

        transaction = self._transaction()
        async with transaction() as tx:
            repository = self.repository.with_prisma(tx)
            updated = await update(repository)
            if not updated:
                return False
            await persist_batch_webhook_terminal_audit(
                audit_service=self.audit_service,
                audit_repository=AuditRepository(tx),
                repository=repository,
                record=record,
                worker_id=self.worker_id,
                outcome=outcome,
                reason=reason,
                status_code=status_code,
            )
            return True

    def _transaction(self) -> Callable[..., object]:
        transaction = getattr(getattr(self.repository, "prisma", None), "tx", None)
        if not callable(transaction):
            raise RuntimeError("terminal webhook auditing requires transaction support")
        return transaction
