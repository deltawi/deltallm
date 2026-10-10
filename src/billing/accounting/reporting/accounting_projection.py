"""Dedicated worker that projects immutable accounting events to legacy read models."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID, uuid5

from src.audit.delivery import AuditDeliveryClass
from src.billing.spend.spend import SpendTrackingService
from src.db.accounting.reporting.accounting_projection import (
    AccountingProjectionClaim,
    AccountingProjectionEvent,
    AccountingProjectionRepository,
)
from src.db.audit.audit_ingestion import AuditIngestionRepository, AuditOutboxEnvelope
from src.metrics.accounting import (
    increment_accounting_projection,
    observe_accounting_projection_lag,
    set_accounting_projection_backlog,
)
from src.telemetry.lifecycle import WorkerHealth, WorkerState

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AccountingProjectionConfig:
    generation: int
    worker_id: str
    batch_size: int = 64
    lease_seconds: int = 30
    poll_interval_seconds: float = 0.05
    max_concurrent_partitions: int = 4
    maintenance_interval_seconds: float = 1.0
    spend_max_pending_events: int = 100_000
    spend_max_attempts: int = 10
    audit_max_pending_events: int = 100_000
    audit_required_reserve: int = 10_000
    audit_max_attempts: int = 10


class AccountingCompatibilityProjector:
    """Idempotent sink adapter; source events remain the durable truth."""

    def __init__(
        self,
        *,
        spend: SpendTrackingService,
        audit: AuditIngestionRepository,
        config: AccountingProjectionConfig,
    ) -> None:
        self.spend = spend
        self.audit = audit
        self.config = config

    async def project(self, event: AccountingProjectionEvent) -> None:
        await self.project_batch([event])

    async def project_batch(self, events: list[AccountingProjectionEvent]) -> None:
        """Project one source batch with bounded, idempotent sink operations."""

        spend_events: list[tuple[str, str, dict[str, object]]] = []
        audit_by_organization: dict[str | None, list[AuditOutboxEnvelope]] = defaultdict(list)
        for event in events:
            if event.outcome == "completed":
                spend_payload = event.payload.get("spend")
                if not isinstance(spend_payload, dict):
                    raise ValueError("completed accounting event is missing its spend payload")
                spend_events.append(
                    (event.event_id, "spend", _restore_spend_datetimes(spend_payload))
                )
            envelope = self._audit_envelope(event)
            audit_by_organization[envelope.organization_id].append(envelope)

        if spend_events:
            await self.spend.log_batch_once(spend_events)
        for envelopes in audit_by_organization.values():
            result = await self.audit.enqueue_bundle(
                envelopes=envelopes,
                max_pending_events=self.config.audit_max_pending_events,
                required_reserve=self.config.audit_required_reserve,
            )
            if any(status == "full" for status in result.statuses.values()):
                raise RuntimeError("audit compatibility projection is at capacity")

    def _audit_envelope(self, event: AccountingProjectionEvent) -> AuditOutboxEnvelope:
        envelope = event.audit_envelope
        payload = envelope.get("payload")
        redacted_payload = envelope.get("redacted_payload", payload)
        if not isinstance(payload, dict) or not isinstance(redacted_payload, dict):
            raise ValueError("accounting audit envelope is missing its redacted payload")
        return AuditOutboxEnvelope(
            event_id=_audit_event_id(event),
            record_type=str(envelope.get("record_type") or "audit_event"),
            organization_id=_optional_string(envelope.get("organization_id")),
            delivery_class=AuditDeliveryClass.REQUIRED,
            payload=payload,
            redacted_payload=redacted_payload,
            max_attempts=self.config.audit_max_attempts,
        )


class AccountingProjectionWorker:
    PROJECTION_NAME = "legacy-spend-audit-v1"

    def __init__(
        self,
        repository: AccountingProjectionRepository,
        projector: AccountingCompatibilityProjector,
        config: AccountingProjectionConfig,
    ) -> None:
        self.repository = repository
        self.projector = projector
        self.config = config
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._next_maintenance_at = 0.0

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    @property
    def worker_health(self) -> WorkerHealth:
        if self._closed:
            return WorkerHealth(WorkerState.DISABLED)
        if self._task is None:
            return WorkerHealth(WorkerState.STARTING)
        if self._task.done():
            detail = None
            if not self._task.cancelled():
                error = self._task.exception()
                detail = type(error).__name__ if error is not None else "stopped"
            return WorkerHealth(WorkerState.FAILED, detail)
        return WorkerHealth(WorkerState.READY)

    async def start(self) -> None:
        self._closed = False
        await self.repository.initialize(
            projection_name=self.PROJECTION_NAME,
            generation=self.config.generation,
        )
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="accounting-projection-worker")

    async def stop(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def run_once(self) -> int:
        recovered, rolled = await self._run_maintenance_if_due()
        claims = await self.repository.claim_many(
            projection_name=self.PROJECTION_NAME,
            generation=self.config.generation,
            worker_id=self.config.worker_id,
            lease_seconds=self.config.lease_seconds,
            limit=self.config.max_concurrent_partitions,
        )
        increment_accounting_projection("recovered", "success", recovered)
        increment_accounting_projection("window_rolled", "success", rolled)
        if not claims:
            return recovered + rolled
        results = await asyncio.gather(
            *(self._project_claim(claim) for claim in claims),
            return_exceptions=True,
        )
        projected = 0
        first_error: BaseException | None = None
        for result in results:
            if isinstance(result, BaseException):
                first_error = first_error or result
            else:
                projected += result
        if first_error is not None:
            raise first_error
        return recovered + rolled + projected

    async def _run_maintenance_if_due(self) -> tuple[int, int]:
        now = monotonic()
        if now < self._next_maintenance_at:
            return 0, 0
        self._next_maintenance_at = now + self.config.maintenance_interval_seconds
        recovered, rolled, backlog = await asyncio.gather(
            self.repository.reconcile_expired(
                generation=self.config.generation,
                limit=self.config.batch_size,
            ),
            self.repository.roll_budget_windows(
                generation=self.config.generation,
                limit=self.config.batch_size,
            ),
            self.repository.backlog(
                projection_name=self.PROJECTION_NAME,
                generation=self.config.generation,
            ),
        )
        set_accounting_projection_backlog(*backlog)
        return recovered, rolled

    async def _project_claim(self, claim: AccountingProjectionClaim) -> int:
        try:
            events = await self.repository.read_batch(claim, limit=self.config.batch_size)
            await self.projector.project_batch(events)
            observed_at = datetime.now(UTC)
            for event in events:
                observe_accounting_projection_lag((observed_at - event.occurred_at).total_seconds())
            completed = await self.repository.complete(
                claim,
                last_sequence=events[-1].sequence if events else None,
            )
            if not completed:
                raise RuntimeError("accounting projection lease was lost")
            increment_accounting_projection("event_projected", "success", len(events))
            return len(events)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.repository.fail(claim, error_code=type(exc).__name__)
            raise

    async def _run(self) -> None:
        failures = 0
        while not self._closed:
            try:
                count = await self.run_once()
                failures = 0
                if count == 0:
                    await asyncio.sleep(self.config.poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
                increment_accounting_projection("iteration", "error")
                logger.exception("accounting projection iteration failed")
                await asyncio.sleep(min(5.0, 0.05 * 2 ** min(failures, 6)))


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _audit_event_id(event: AccountingProjectionEvent) -> str:
    candidate = event.audit_envelope.get("event_id")
    try:
        return str(UUID(str(candidate)))
    except (ValueError, TypeError, AttributeError):
        pass
    try:
        namespace = UUID(event.event_id)
    except ValueError:
        # Recovery keys have suffixes, not UUID syntax. Match the native audit
        # identity without changing already-issued UUID-source identities.
        digest = hashlib.md5(
            ("deltallm-accounting-audit:v2:" + event.event_id).encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()
        return str(UUID(hex=digest))
    return str(uuid5(namespace, "accounting-audit:v1"))


def _restore_spend_datetimes(payload: dict[str, object]) -> dict[str, object]:
    restored = dict(payload)
    for field in ("start_time", "end_time"):
        value = restored.get(field)
        if isinstance(value, str):
            restored[field] = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return restored
