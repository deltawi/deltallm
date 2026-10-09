from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from src.db.audit_ingestion import AuditIngestionRepository
from src.db.client import is_prisma_transaction_client
from src.db.email import EmailOutboxRepository
from src.db.repositories import AuditRepository
from src.db.spend_ingestion import SpendIngestionRepository
from src.models.errors import ServiceUnavailableError


TelemetryQueueName = Literal["spend", "audit", "email_delivery_audit"]
ReplayAuditWriter = Callable[[AuditRepository], Awaitable[None]]


class TelemetryReplayUnavailableError(ServiceUnavailableError):
    error_type = "telemetry_replay_unavailable"
    message = "Telemetry replay is temporarily unavailable"

    def __init__(self) -> None:
        super().__init__(code="telemetry_replay_unavailable")


class TelemetryReplayService:
    """Atomically replay blocked telemetry and persist its required operator audit."""

    def __init__(self, db_client: Any | None, *, audit_service: object | None) -> None:
        self.db = db_client
        self.audit_service = audit_service

    async def replay_blocked(
        self,
        *,
        queue_name: TelemetryQueueName,
        event_id: str,
        replayed_by: str,
        audit_writer: ReplayAuditWriter,
    ) -> bool:
        if self.db is None or self.audit_service is None:
            raise TelemetryReplayUnavailableError()
        try:
            async with self._transaction() as tx:
                if queue_name == "spend":
                    replayed = await SpendIngestionRepository(tx).replay_blocked(
                        event_id=event_id,
                        replayed_by=replayed_by,
                    )
                elif queue_name == "audit":
                    replayed = await AuditIngestionRepository(tx).replay_blocked(
                        event_id=event_id,
                        replayed_by=replayed_by,
                    )
                else:
                    replayed = await EmailOutboxRepository(tx).replay_blocked_delivery_audit(
                        email_id=event_id,
                        replayed_by=replayed_by,
                    )
                if not replayed:
                    return False
                await audit_writer(AuditRepository(tx))
        except asyncio.CancelledError:
            raise
        except TelemetryReplayUnavailableError:
            raise
        except Exception as exc:
            raise TelemetryReplayUnavailableError() from exc
        return True

    async def resolve_unknown_email_delivery(
        self,
        *,
        email_id: str,
        resolution: Literal["sent", "failed"],
        audit_writer: ReplayAuditWriter,
    ) -> bool:
        """Resolve an ambiguous external side effect with an atomic operator audit."""

        if self.db is None or self.audit_service is None:
            raise TelemetryReplayUnavailableError()
        try:
            async with self._transaction() as tx:
                resolved = await EmailOutboxRepository(tx).resolve_unknown_delivery(
                    email_id=email_id,
                    resolution=resolution,
                )
                if not resolved:
                    return False
                await audit_writer(AuditRepository(tx))
        except asyncio.CancelledError:
            raise
        except TelemetryReplayUnavailableError:
            raise
        except Exception as exc:
            raise TelemetryReplayUnavailableError() from exc
        return True

    @asynccontextmanager
    async def _transaction(self):  # noqa: ANN202
        if self.db is None:
            raise TelemetryReplayUnavailableError()
        if is_prisma_transaction_client(self.db):
            yield self.db
            return
        tx_factory = getattr(self.db, "tx", None)
        if not callable(tx_factory):
            raise TelemetryReplayUnavailableError()
        async with tx_factory() as tx:
            yield tx
