from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
import inspect
import logging
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from time import perf_counter
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from src.audit.actions import AuditAction
from src.audit.delivery import AuditDeliveryClass, parse_audit_delivery_class
from src.db.audit_ingestion import (
    AuditIngestionRepository,
    AuditOutboxEnvelope,
    AuditOutboxRecord,
)
from src.db.client import is_prisma_transaction_client
from src.db.errors import is_record_specific_database_error
from src.db.repositories import AuditEventRecord, AuditPayloadRecord, AuditRepository
from src.metrics import (
    increment_audit_events_dropped,
    increment_audit_write_failure,
    observe_audit_ingestion_latency,
    set_audit_queue_depth,
)
from src.metrics.audit import (
    increment_audit_cleanup,
    increment_audit_enqueue,
    set_audit_capacity_utilization,
    set_audit_oldest_event_age,
)
from src.models.errors import ServiceUnavailableError
from src.redis_namespace import build_redis_channel
from src.services.audit_policy_invalidation import AuditPolicyInvalidation
from src.telemetry.prompt_render import PromptRenderEvent
from src.telemetry.lifecycle import (
    WorkerHealth,
    WorkerState,
    stop_tasks_before_deadline,
    task_failure_detail,
    wait_for_startup,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.db.prompt_registry import PromptRegistryRepository


class AuditIngestionPath(StrEnum):
    SYNC = "sync"
    QUEUE = "queue"
    FALLBACK = "fallback"


class AuditDropReason(StrEnum):
    SERVICE_CLOSED = "service_closed"
    QUEUE_FULL_NON_CRITICAL = "queue_full_non_critical"
    DURABLE_ENQUEUE_UNAVAILABLE = "durable_enqueue_unavailable"


@dataclass
class AuditPayloadInput:
    kind: str
    storage_mode: str = "inline"
    content_json: dict[str, Any] | None = None
    storage_uri: str | None = None
    content_sha256: str | None = None
    size_bytes: int | None = None
    redacted: bool = False

    def has_content(self) -> bool:
        return self.content_json is not None or self.storage_uri is not None


@dataclass
class AuditEventInput:
    action: str
    organization_id: str | None = None
    actor_type: str | None = None
    actor_id: str | None = None
    api_key: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    status: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_type: str | None = None
    error_code: str | None = None
    metadata: dict[str, Any] | None = None
    prev_hash: str | None = None
    event_hash: str | None = None
    event_id: str | None = None


@dataclass
class _QueueItem:
    event: AuditEventInput
    payloads: list[AuditPayloadInput] = field(default_factory=list)
    critical: bool = False
    event_id: str = ""
    use_policy_cache: bool = True


@dataclass(frozen=True, slots=True)
class AuditIngestionConfig:
    enabled: bool = False
    worker_enabled: bool = True
    batch_size: int = 100
    flush_interval_seconds: float = 0.1
    lease_seconds: int = 30
    max_attempts: int = 10
    max_pending_events: int = 100_000
    required_reserve: int = 10_000
    completed_retention_hours: int = 1
    failed_retention_days: int = 30
    cleanup_interval_seconds: float = 60.0
    cleanup_batch_size: int = 1000
    cleanup_max_batches_per_run: int = 10
    cleanup_time_budget_seconds: float = 2.0
    worker_startup_timeout_seconds: float = 5.0
    shutdown_drain_timeout_seconds: float = 20.0
    worker_id: str = "gateway-audit"


class AuditIngestionOverloadedError(ServiceUnavailableError):
    error_type = "audit_ingestion_overloaded"
    message = "Audit ingestion backlog is at capacity"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message=message, code="audit_ingestion_capacity")


class RequiredAuditPersistenceError(ServiceUnavailableError):
    error_type = "audit_persistence_unavailable"
    message = "Required audit persistence is unavailable"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message=message, code="audit_persistence_unavailable")


def require_audit_service(service: AuditService | None) -> AuditService:
    """Resolve a required audit dependency without silently disabling it."""

    if service is None:
        raise RequiredAuditPersistenceError()
    return service


async def enqueue_audit_event(
    service: Any,
    event: AuditEventInput,
    *,
    payloads: list[AuditPayloadInput] | None = None,
    delivery_class: AuditDeliveryClass | str = AuditDeliveryClass.BEST_EFFORT,
) -> Any:
    """Compatibility adapter for durable services and legacy test/plugin sinks."""

    normalized_delivery = parse_audit_delivery_class(delivery_class)
    try:
        enqueue = getattr(service, "enqueue_event", None)
        if callable(enqueue):
            return await enqueue(event, payloads=payloads, delivery_class=normalized_delivery)
        record = getattr(service, "record_event", None)
        if not callable(record):
            return None
        result = record(
            event,
            payloads=payloads,
            critical=normalized_delivery is AuditDeliveryClass.REQUIRED,
        )
        if inspect.isawaitable(result):
            return await result
        return result
    except asyncio.CancelledError:
        raise
    except AuditIngestionOverloadedError:
        if normalized_delivery is AuditDeliveryClass.REQUIRED:
            raise
        increment_audit_events_dropped(reason=AuditDropReason.QUEUE_FULL_NON_CRITICAL.value)
        logger.warning(
            "best-effort audit compatibility sink is full; dropping event",
            extra={"action": event.action},
        )
        return "dropped"
    except RequiredAuditPersistenceError:
        if normalized_delivery is AuditDeliveryClass.REQUIRED:
            raise
        increment_audit_write_failure(path="compat_best_effort_enqueue")
        increment_audit_events_dropped(reason=AuditDropReason.DURABLE_ENQUEUE_UNAVAILABLE.value)
        logger.exception(
            "best-effort audit compatibility sink failed; dropping event",
            extra={"action": event.action},
        )
        return "dropped"
    except Exception as exc:
        if normalized_delivery is AuditDeliveryClass.REQUIRED:
            increment_audit_write_failure(path="compat_required_enqueue")
            logger.exception(
                "required audit compatibility sink failed",
                extra={"action": event.action},
            )
            raise RequiredAuditPersistenceError() from exc
        increment_audit_write_failure(path="compat_best_effort_enqueue")
        increment_audit_events_dropped(reason=AuditDropReason.DURABLE_ENQUEUE_UNAVAILABLE.value)
        logger.exception(
            "best-effort audit compatibility sink failed; dropping event",
            extra={"action": event.action},
        )
        return "dropped"


class AuditService:
    def __init__(
        self,
        repository: AuditRepository,
        *,
        db_client: Any | None = None,
        prompt_repository: PromptRegistryRepository | None = None,
        ingestion_config: AuditIngestionConfig | None = None,
        redis_client: Any | None = None,
        policy_invalidation_channel: str | None = None,
        queue_max_size: int = 1024,
        critical_retry_attempts: int = 3,
        content_policy_cache_ttl_seconds: float = 30.0,
        content_policy_cache_max_entries: int = 10_000,
    ) -> None:
        self.repository = repository
        self.db = db_client
        self.prompt_repository = prompt_repository
        self.ingestion_config = ingestion_config or AuditIngestionConfig()
        self.ingestion_repository = AuditIngestionRepository(db_client)
        resolved_policy_channel = policy_invalidation_channel or build_redis_channel(
            application="deltallm",
            environment="dev",
            schema_version=1,
            capability="audit-content-policy-invalidation",
        )
        bounded_size = max(1, queue_max_size)
        self._queue: asyncio.Queue[_QueueItem] = asyncio.Queue(maxsize=bounded_size)
        self._worker_task: asyncio.Task[Any] | None = None
        self._cleanup_task: asyncio.Task[Any] | None = None
        self._durable_worker_running = False
        self._worker_started = asyncio.Event()
        self._cleanup_started = asyncio.Event()
        self._worker_state = WorkerState.DISABLED
        self._worker_detail: str | None = None
        self._critical_retry_attempts = max(1, int(critical_retry_attempts))
        self._content_policy_cache_ttl_seconds = max(0.1, float(content_policy_cache_ttl_seconds))
        self._content_policy_cache_max_entries = max(1, int(content_policy_cache_max_entries))
        self._content_policy_cache: OrderedDict[str, tuple[float, bool]] = OrderedDict()
        self.policy_invalidation = AuditPolicyInvalidation(
            redis_client=redis_client,
            channel=resolved_policy_channel,
            invalidate_one=self.invalidate_content_storage_policy,
            invalidate_all=self._content_policy_cache.clear,
        )
        self._closed = False
        self._started = False
        self._wake = asyncio.Event()
        self.dropped_events = 0
        self.failed_events = 0

    @property
    def durable_ingestion_enabled(self) -> bool:
        return self.ingestion_config.enabled

    @property
    def worker_health(self) -> WorkerHealth:
        expected = not self._closed and (
            not self.ingestion_config.enabled or self.ingestion_config.worker_enabled
        )
        if not expected:
            return WorkerHealth(WorkerState.DISABLED)
        tasks = [("audit ingestion worker", self._worker_task)]
        if self.ingestion_config.enabled:
            tasks.append(("audit ingestion cleanup worker", self._cleanup_task))
        for name, task in tasks:
            detail = task_failure_detail(task)
            if detail is not None:
                return WorkerHealth(WorkerState.FAILED, f"{name}: {detail}")
            if task is None:
                return WorkerHealth(WorkerState.FAILED, f"{name}: expected task is missing")
        return WorkerHealth(self._worker_state, self._worker_detail)

    @property
    def policy_listener_health(self) -> WorkerHealth:
        return self.policy_invalidation.health

    async def start(self) -> None:
        if self._started:
            if self.worker_health.ready:
                return
            raise RuntimeError("audit service was started but its ingestion worker is not healthy")
        self._closed = False
        if self.ingestion_config.enabled:
            if self.db is None:
                self._worker_state = WorkerState.FAILED
                self._worker_detail = "audit outbox mode requires the telemetry database pool"
                raise RuntimeError("audit outbox mode requires the telemetry database pool")
            try:
                await asyncio.wait_for(
                    self._reconcile_durable_capacity(),
                    timeout=self.ingestion_config.worker_startup_timeout_seconds,
                )
            except Exception as exc:
                self._worker_state = WorkerState.FAILED
                self._worker_detail = f"capacity reconciliation failed: {type(exc).__name__}: {exc}"
                raise
            if self.ingestion_config.worker_enabled:
                self._launch_durable_worker_tasks()
                await self._await_worker_startup()
            else:
                self._worker_state = WorkerState.DISABLED
        else:
            set_audit_queue_depth(self._queue.qsize())
            self._worker_started.clear()
            self._worker_state = WorkerState.STARTING
            self._worker_detail = None
            self._worker_task = asyncio.create_task(self._worker_loop())
            await wait_for_startup(
                started=self._worker_started,
                task=self._worker_task,
                timeout_seconds=self.ingestion_config.worker_startup_timeout_seconds,
                worker_name="audit queue worker",
            )
            self._worker_state = WorkerState.READY
        await self.policy_invalidation.start(
            timeout_seconds=self.ingestion_config.worker_startup_timeout_seconds
        )
        self._started = True

    async def shutdown(self) -> None:
        self._closed = True
        self._worker_state = WorkerState.STOPPING
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.ingestion_config.shutdown_drain_timeout_seconds
        if self._worker_task is not None:
            if self.ingestion_config.enabled:
                await self._stop_durable_worker_tasks(deadline=deadline)
            else:
                join_task = asyncio.create_task(self._queue.join())
                joined = await stop_tasks_before_deadline(
                    [join_task],
                    deadline=deadline,
                )
                worker_stopped = await stop_tasks_before_deadline(
                    [self._worker_task],
                    deadline=deadline,
                    cancel_first=True,
                )
                if not joined or not worker_stopped:
                    increment_audit_write_failure(path="shutdown_timeout")
                    logger.error(
                        "audit queue worker exceeded its shutdown deadline and was cancelled"
                    )
                self._worker_task = None
        if self._cleanup_task is not None:
            await stop_tasks_before_deadline(
                [self._cleanup_task],
                deadline=deadline,
                cancel_first=True,
            )
            self._cleanup_task = None
        listener_stopped = await self.policy_invalidation.shutdown(deadline=deadline)
        if not listener_stopped:
            increment_audit_write_failure(path="policy_listener_shutdown_timeout")
            logger.error("audit policy listener exceeded its shutdown deadline and was cancelled")
        self._worker_state = WorkerState.DISABLED
        self._worker_detail = None
        self._started = False
        set_audit_queue_depth(0)

    async def reconfigure(self, config: AuditIngestionConfig) -> None:
        if config.enabled != self.ingestion_config.enabled:
            raise RuntimeError("changing audit ingestion mode requires a restart")
        if not config.enabled:
            # Legacy ingestion owns a continuously running in-memory queue
            # worker; worker_enabled applies only to durable outbox consumers.
            self.ingestion_config = config
            return
        previous = self.ingestion_config
        worker_active = self._worker_task is not None and not self._worker_task.done()
        worker_desired = config.enabled and config.worker_enabled and self.db is not None
        if worker_active and not worker_desired:
            await self._stop_durable_worker_tasks()
        self.ingestion_config = config
        if worker_desired and not worker_active:
            try:
                await self._reconcile_durable_capacity()
                self._launch_durable_worker_tasks()
                await self._await_worker_startup()
            except Exception:
                self.ingestion_config = previous
                raise

    async def _reconcile_durable_capacity(self) -> None:
        try:
            await self.ingestion_repository.reconcile_capacity()
        except Exception:
            increment_audit_write_failure(path="capacity_reconcile")
            logger.exception("failed to reconcile durable audit capacity")
            raise

    def _launch_durable_worker_tasks(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._durable_worker_running = True
        self._worker_started.clear()
        self._cleanup_started.clear()
        self._worker_state = WorkerState.STARTING
        self._worker_detail = None
        self._worker_task = asyncio.create_task(self._durable_worker_loop())
        self._cleanup_task = asyncio.create_task(self._durable_cleanup_loop())

    async def _await_worker_startup(self) -> None:
        if self._worker_task is None or self._cleanup_task is None:
            raise RuntimeError("audit worker tasks were not launched")
        deadline = (
            asyncio.get_running_loop().time() + self.ingestion_config.worker_startup_timeout_seconds
        )
        try:
            await wait_for_startup(
                started=self._worker_started,
                task=self._worker_task,
                timeout_seconds=max(0.0, deadline - asyncio.get_running_loop().time()),
                worker_name="durable audit worker",
            )
            await wait_for_startup(
                started=self._cleanup_started,
                task=self._cleanup_task,
                timeout_seconds=max(0.0, deadline - asyncio.get_running_loop().time()),
                worker_name="durable audit cleanup worker",
            )
        except Exception as exc:
            self._durable_worker_running = False
            self._worker_state = WorkerState.FAILED
            self._worker_detail = f"{type(exc).__name__}: {exc}"
            await stop_tasks_before_deadline(
                [self._worker_task, self._cleanup_task],
                deadline=asyncio.get_running_loop().time(),
                cancel_first=True,
            )
            raise
        self._worker_state = WorkerState.READY

    async def _stop_durable_worker_tasks(self, *, deadline: float | None = None) -> None:
        if deadline is None:
            deadline = (
                asyncio.get_running_loop().time()
                + self.ingestion_config.shutdown_drain_timeout_seconds
            )
        self._worker_state = WorkerState.STOPPING
        self._durable_worker_running = False
        self._wake.set()
        worker = self._worker_task
        cleanup_stopped = await stop_tasks_before_deadline(
            [self._cleanup_task],
            deadline=deadline,
            cancel_first=True,
        )
        worker_stopped = await stop_tasks_before_deadline([worker], deadline=deadline)
        if not cleanup_stopped or not worker_stopped:
            increment_audit_write_failure(path="shutdown_timeout")
            logger.error("durable audit worker exceeded its shutdown deadline and was cancelled")
        self._worker_task = None
        self._cleanup_task = None
        self._worker_state = WorkerState.DISABLED
        self._worker_detail = None

    async def enqueue_event(
        self,
        event: AuditEventInput,
        *,
        payloads: list[AuditPayloadInput] | None = None,
        delivery_class: AuditDeliveryClass | str = AuditDeliveryClass.BEST_EFFORT,
    ) -> str:
        normalized_delivery = parse_audit_delivery_class(delivery_class)
        required = normalized_delivery is AuditDeliveryClass.REQUIRED
        item = _QueueItem(
            event=event,
            payloads=list(payloads or []),
            critical=required,
            event_id=str(event.event_id or uuid4()),
            use_policy_cache=not self.ingestion_config.enabled,
        )
        if not self.ingestion_config.enabled:
            if required:
                try:
                    await self.record_event_sync(
                        event,
                        payloads=item.payloads,
                        event_id=item.event_id,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    increment_audit_write_failure(path="legacy_sync_required")
                    logger.exception(
                        "failed to persist required legacy audit event",
                        extra={"action": event.action},
                    )
                    raise RequiredAuditPersistenceError() from exc
                return "persisted"
            return self._enqueue_legacy_audit(item)
        if self._closed:
            if required:
                raise RequiredAuditPersistenceError("Audit service is closed")
            self.dropped_events += 1
            increment_audit_events_dropped(reason=AuditDropReason.SERVICE_CLOSED.value)
            increment_audit_enqueue(
                record_type="audit_event",
                delivery_class=normalized_delivery.value,
                outcome="dropped_closed",
            )
            return "dropped"

        payload = _serialize_audit_item(item)
        redacted_payload = _serialize_audit_item(_redact_audit_item(item))
        try:
            result = await self.ingestion_repository.enqueue(
                event_id=item.event_id,
                record_type="audit_event",
                organization_id=event.organization_id,
                delivery_class=normalized_delivery,
                payload=payload,
                redacted_payload=redacted_payload,
                max_attempts=self.ingestion_config.max_attempts,
                max_pending_events=self.ingestion_config.max_pending_events,
                required_reserve=self.ingestion_config.required_reserve,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            increment_audit_write_failure(path="outbox_enqueue")
            logger.exception(
                "failed to enqueue audit event",
                extra={"action": event.action, "delivery_class": normalized_delivery.value},
            )
            if required:
                raise RequiredAuditPersistenceError() from exc
            self.dropped_events += 1
            increment_audit_events_dropped(reason=AuditDropReason.DURABLE_ENQUEUE_UNAVAILABLE.value)
            increment_audit_enqueue(
                record_type="audit_event",
                delivery_class=normalized_delivery.value,
                outcome="dropped_dependency",
            )
            return "dropped"
        increment_audit_enqueue(
            record_type="audit_event",
            delivery_class=normalized_delivery.value,
            outcome=result.status,
        )
        set_audit_queue_depth(result.pending_count)
        set_audit_capacity_utilization(
            pending=result.pending_count,
            capacity=self.ingestion_config.max_pending_events,
        )
        if result.status == "full":
            if required:
                raise AuditIngestionOverloadedError("audit ingestion backlog is at capacity")
            self.dropped_events += 1
            increment_audit_events_dropped(reason=AuditDropReason.QUEUE_FULL_NON_CRITICAL.value)
            return "dropped"
        self._wake.set()
        return result.status

    async def enqueue_prompt_render(self, event: PromptRenderEvent) -> str:
        redacted_event = event.redacted()
        if not self.ingestion_config.enabled:
            if self.prompt_repository is None:
                raise RequiredAuditPersistenceError("Prompt render persistence is unavailable")
            try:
                await self._persist_prompt_render_with_policy(
                    event=event,
                    redacted_event=redacted_event,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                increment_audit_write_failure(path="legacy_sync_prompt_render")
                logger.exception("failed to persist required legacy prompt render")
                raise RequiredAuditPersistenceError() from exc
            self._enqueue_legacy_audit(_prompt_render_audit_item(event))
            return "persisted"

        prompt_envelope = AuditOutboxEnvelope(
            event_id=event.prompt_render_log_id,
            record_type="prompt_render",
            organization_id=event.organization_id,
            delivery_class=AuditDeliveryClass.REQUIRED,
            payload=event.persistence_payload(),
            redacted_payload=redacted_event.persistence_payload(),
            max_attempts=self.ingestion_config.max_attempts,
        )
        audit_item = _prompt_render_audit_item(event)
        audit_payload = _serialize_audit_item(audit_item)
        audit_envelope = AuditOutboxEnvelope(
            event_id=audit_item.event_id,
            record_type="audit_event",
            organization_id=event.organization_id,
            delivery_class=AuditDeliveryClass.BEST_EFFORT,
            payload=audit_payload,
            redacted_payload=audit_payload,
            max_attempts=self.ingestion_config.max_attempts,
        )
        try:
            result = await self.ingestion_repository.enqueue_bundle(
                envelopes=[prompt_envelope, audit_envelope],
                max_pending_events=self.ingestion_config.max_pending_events,
                required_reserve=self.ingestion_config.required_reserve,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            increment_audit_write_failure(path="outbox_enqueue_prompt_render")
            logger.exception("failed to enqueue required prompt render")
            raise RequiredAuditPersistenceError() from exc

        prompt_status = result.statuses.get(prompt_envelope.event_id, "full")
        audit_status = result.statuses.get(audit_envelope.event_id, "full")
        for envelope, outcome in (
            (prompt_envelope, prompt_status),
            (audit_envelope, audit_status),
        ):
            increment_audit_enqueue(
                record_type=envelope.record_type,
                delivery_class=envelope.delivery_class,
                outcome=outcome,
            )
        set_audit_queue_depth(result.pending_count)
        set_audit_capacity_utilization(
            pending=result.pending_count,
            capacity=self.ingestion_config.max_pending_events,
        )
        if prompt_status == "full":
            raise AuditIngestionOverloadedError("audit ingestion backlog is at capacity")
        if audit_status == "full":
            self.dropped_events += 1
            increment_audit_events_dropped(reason=AuditDropReason.QUEUE_FULL_NON_CRITICAL.value)
        if prompt_status == "accepted" or audit_status == "accepted":
            self._wake.set()
        return prompt_status

    async def _persist_prompt_render_with_policy(
        self,
        *,
        event: PromptRenderEvent,
        redacted_event: PromptRenderEvent,
    ) -> None:
        if self.prompt_repository is None:
            raise RuntimeError("prompt render repository is unavailable")
        organization_id = event.organization_id
        target_db = self.prompt_repository.prisma or self.db
        if not organization_id or target_db is None:
            await self.prompt_repository.create_render_log(**redacted_event.render_log_payload())
            return
        async with self._transaction(target_db) as tx:
            ingestion_repository = self.ingestion_repository.with_db(tx)
            await ingestion_repository.lock_content_policy(organization_id)
            enabled, _ = await ingestion_repository.get_content_policy(organization_id)
            prompt_repository = self.prompt_repository.with_db(tx)
            selected_event = event if enabled else redacted_event
            await prompt_repository.create_render_log(**selected_event.render_log_payload())

    def record_event(
        self,
        event: AuditEventInput,
        *,
        payloads: list[AuditPayloadInput] | None = None,
        critical: bool = False,
    ) -> None:
        if critical:
            raise RuntimeError(
                "critical audit events must use await audit_service.enqueue_event(..., delivery_class='required')"
            )
        if self.ingestion_config.enabled:
            self.dropped_events += 1
            increment_audit_events_dropped(reason=AuditDropReason.QUEUE_FULL_NON_CRITICAL.value)
            logger.warning(
                "legacy synchronous audit producer used while durable ingestion is enabled; dropping best-effort event",
                extra={"action": event.action},
            )
            return
        if self._closed:
            logger.warning(
                "audit service is closed; dropping event", extra={"action": event.action}
            )
            self.dropped_events += 1
            increment_audit_events_dropped(reason=AuditDropReason.SERVICE_CLOSED.value)
            return

        item = _QueueItem(event=event, payloads=list(payloads or []), critical=critical)
        try:
            self._queue.put_nowait(item)
            set_audit_queue_depth(self._total_queue_depth())
        except asyncio.QueueFull:
            self.dropped_events += 1
            increment_audit_events_dropped(reason=AuditDropReason.QUEUE_FULL_NON_CRITICAL.value)
            logger.warning(
                "audit queue full; dropping non-critical event", extra={"action": event.action}
            )

    def _enqueue_legacy_audit(self, item: _QueueItem) -> str:
        if self._closed:
            self.dropped_events += 1
            increment_audit_events_dropped(reason=AuditDropReason.SERVICE_CLOSED.value)
            return "dropped"
        try:
            self._queue.put_nowait(item)
            set_audit_queue_depth(self._total_queue_depth())
            return "queued"
        except asyncio.QueueFull:
            self.dropped_events += 1
            increment_audit_events_dropped(reason=AuditDropReason.QUEUE_FULL_NON_CRITICAL.value)
            logger.error(
                "legacy audit queue full; dropping event",
                extra={"action": item.event.action},
            )
            return "dropped"

    async def record_event_sync(
        self,
        event: AuditEventInput,
        *,
        payloads: list[AuditPayloadInput] | None = None,
        repository: AuditRepository | None = None,
        event_id: str | None = None,
    ) -> None:
        await self._persist(
            _QueueItem(
                event=event,
                payloads=list(payloads or []),
                critical=True,
                event_id=event_id or event.event_id or str(uuid4()),
            ),
            repository=repository,
        )

    async def _worker_loop(self) -> None:
        self._worker_started.set()
        while True:
            item = await self._queue.get()
            try:
                await self._persist_with_bounded_retry(item)
            finally:
                self._queue.task_done()
                set_audit_queue_depth(self._total_queue_depth())

    def _total_queue_depth(self) -> int:
        return self._queue.qsize()

    async def _durable_worker_loop(self) -> None:
        self._worker_started.set()
        consecutive_failures = 0
        while self._durable_worker_running and not self._closed:
            try:
                await self._durable_worker_iteration()
                consecutive_failures = 0
                self._worker_state = WorkerState.READY
                self._worker_detail = None
            except asyncio.CancelledError:
                raise
            except Exception:
                consecutive_failures += 1
                self._worker_state = (
                    WorkerState.FAILED if consecutive_failures >= 3 else WorkerState.DEGRADED
                )
                self._worker_detail = (
                    f"worker iteration failed {consecutive_failures} consecutive time(s)"
                )
                increment_audit_write_failure(path="worker_iteration")
                logger.exception("durable audit worker iteration failed; continuing")
                await asyncio.sleep(min(5.0, 0.1 * (2 ** min(consecutive_failures - 1, 6))))

    async def _durable_worker_iteration(self) -> None:
        try:
            records = await self.ingestion_repository.claim_batch(
                limit=self.ingestion_config.batch_size,
                worker_id=self.ingestion_config.worker_id,
                claim_token=str(uuid4()),
                lease_seconds=self.ingestion_config.lease_seconds,
            )
        except Exception:
            increment_audit_write_failure(path="claim")
            raise
        if not records:
            self._wake.clear()
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=self.ingestion_config.flush_interval_seconds,
                )
            except TimeoutError:
                pass
            await self._publish_durable_backlog()
            return
        await self._process_durable_batch(records)
        await self._publish_durable_backlog()

    async def _process_durable_batch(self, records: list[AuditOutboxRecord]) -> None:
        valid: list[AuditOutboxRecord] = []
        for record in records:
            try:
                self._validate_durable_record(record)
            except Exception as exc:
                increment_audit_write_failure(path="validation")
                await self._safe_mark_durable_retry(record, exc)
            else:
                valid.append(record)
        if valid:
            await self._commit_durable_with_isolation(valid)

    @staticmethod
    def _validate_durable_record(record: AuditOutboxRecord) -> None:
        if record.record_type == "audit_event":
            _deserialize_audit_item(record.payload, event_id=record.event_id)
            _deserialize_audit_item(record.redacted_payload, event_id=record.event_id)
            return
        if record.record_type == "prompt_render":
            PromptRenderEvent.from_persistence_payload(record.payload)
            PromptRenderEvent.from_persistence_payload(record.redacted_payload)
            return
        raise ValueError(f"unsupported audit outbox record type: {record.record_type}")

    async def _commit_durable_with_isolation(
        self,
        records: list[AuditOutboxRecord],
    ) -> None:
        try:
            await self._commit_durable_records(records)
        except Exception as exc:
            increment_audit_write_failure(path="processing")
            if len(records) > 1 and is_record_specific_database_error(exc):
                midpoint = len(records) // 2
                await self._commit_durable_with_isolation(records[:midpoint])
                await self._commit_durable_with_isolation(records[midpoint:])
                return
            logger.exception(
                "failed to persist durable audit batch",
                extra={"batch_size": len(records)},
            )
            for record in records:
                await self._safe_mark_durable_retry(record, exc)

    async def _commit_durable_records(self, records: list[AuditOutboxRecord]) -> None:
        from src.db.prompt_registry import PromptRegistryRepository

        claim_token = _shared_audit_claim_token(records)
        heartbeat = asyncio.create_task(
            self._durable_lease_heartbeat(
                event_ids=[record.event_id for record in records],
                claim_token=claim_token,
            )
        )
        try:
            async with self._transaction() as tx:
                ingestion_repository = self.ingestion_repository.with_db(tx)
                audit_repository = self.repository.with_db(tx)
                prompt_repository = PromptRegistryRepository(tx)
                organization_ids = sorted(
                    {record.organization_id for record in records if record.organization_id}
                )
                await ingestion_repository.lock_content_policies(organization_ids)
                policy_snapshots = await ingestion_repository.get_content_policies(organization_ids)
                policies = {
                    organization_id: enabled
                    for organization_id, (enabled, _version) in policy_snapshots.items()
                }

                disabled_event_ids = [
                    record.event_id
                    for record in records
                    if record.organization_id and not policies.get(record.organization_id, False)
                ]
                await ingestion_repository.redact_claimed_records(
                    event_ids=disabled_event_ids,
                    worker_id=self.ingestion_config.worker_id,
                    claim_token=claim_token,
                )

                audit_events: list[AuditEventRecord] = []
                audit_payloads: list[AuditPayloadRecord] = []
                prompt_renders: list[dict[str, Any]] = []
                for record in records:
                    content_enabled = bool(
                        record.organization_id and policies.get(record.organization_id, False)
                    )
                    payload = record.payload if content_enabled else record.redacted_payload
                    if record.record_type == "audit_event":
                        event_record, payload_records = _build_audit_records(
                            _deserialize_audit_item(payload, event_id=record.event_id),
                            content_enabled=content_enabled,
                        )
                        audit_events.append(event_record)
                        audit_payloads.extend(payload_records)
                    else:
                        render_event = PromptRenderEvent.from_persistence_payload(payload)
                        prompt_renders.append(render_event.render_log_payload())

                started = perf_counter()
                await audit_repository.create_events_batch(audit_events)
                await audit_repository.create_payloads_batch(audit_payloads)
                await prompt_repository.create_render_logs_batch(prompt_renders)
                observe_audit_ingestion_latency(
                    path=AuditIngestionPath.QUEUE.value,
                    latency_seconds=perf_counter() - started,
                )

                completed = await ingestion_repository.mark_completed(
                    event_ids=[record.event_id for record in records],
                    worker_id=self.ingestion_config.worker_id,
                    claim_token=claim_token,
                )
                if completed != len(records):
                    raise RuntimeError(
                        f"audit batch lease ownership changed: completed={completed} "
                        f"expected={len(records)}"
                    )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _durable_lease_heartbeat(
        self,
        *,
        event_ids: list[str],
        claim_token: str,
    ) -> None:
        interval = max(0.1, self.ingestion_config.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await self.ingestion_repository.renew_lease(
                    event_ids=event_ids,
                    worker_id=self.ingestion_config.worker_id,
                    claim_token=claim_token,
                    lease_seconds=self.ingestion_config.lease_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                increment_audit_write_failure(path="lease_heartbeat")
                logger.exception("failed renewing durable audit ingestion lease")
                continue
            if renewed != len(event_ids):
                increment_audit_write_failure(path="lease_lost")
                logger.warning(
                    "durable audit ingestion lease ownership changed during processing",
                    extra={"expected": len(event_ids), "renewed": renewed},
                )
                return

    async def _mark_durable_retry(self, record: AuditOutboxRecord, exc: Exception) -> None:
        terminal = await self.ingestion_repository.mark_retry(
            record=record,
            worker_id=self.ingestion_config.worker_id,
            error=str(exc),
        )
        if terminal:
            self.failed_events += 1

    async def _safe_mark_durable_retry(
        self,
        record: AuditOutboxRecord,
        exc: Exception,
    ) -> None:
        try:
            await self._mark_durable_retry(record, exc)
        except asyncio.CancelledError:
            raise
        except Exception:
            increment_audit_write_failure(path="retry_transition")
            logger.exception(
                "failed to transition durable audit event for retry; lease will expire",
                extra={"event_id": record.event_id},
            )

    async def _publish_durable_backlog(self) -> None:
        try:
            count, oldest_age = await self.ingestion_repository.pending_stats()
        except Exception:
            increment_audit_write_failure(path="backlog_metrics")
            return
        set_audit_queue_depth(count)
        set_audit_oldest_event_age(oldest_age)
        set_audit_capacity_utilization(
            pending=count,
            capacity=self.ingestion_config.max_pending_events,
        )

    async def _durable_cleanup_loop(self) -> None:
        self._cleanup_started.set()
        while not self._closed:
            await asyncio.sleep(max(0.1, self.ingestion_config.cleanup_interval_seconds))
            if not self._closed:
                await self._cleanup_durable()

    async def _cleanup_durable(self) -> int:
        started = perf_counter()
        deleted_total = 0
        for _ in range(max(1, self.ingestion_config.cleanup_max_batches_per_run)):
            if perf_counter() - started >= self.ingestion_config.cleanup_time_budget_seconds:
                break
            try:
                deleted = await self.ingestion_repository.cleanup_terminal(
                    completed_retention_hours=self.ingestion_config.completed_retention_hours,
                    failed_retention_days=self.ingestion_config.failed_retention_days,
                    limit=self.ingestion_config.cleanup_batch_size,
                )
            except Exception:
                increment_audit_write_failure(path="cleanup")
                logger.exception("failed cleaning durable audit outbox")
                break
            deleted_total += deleted
            increment_audit_cleanup(deleted)
            if deleted < self.ingestion_config.cleanup_batch_size:
                break
        return deleted_total

    @asynccontextmanager
    async def _transaction(self, db_client: Any | None = None):  # noqa: ANN202
        target_db = db_client if db_client is not None else self.db
        if target_db is None:
            raise RuntimeError("audit ingestion database is unavailable")
        if is_prisma_transaction_client(target_db):
            yield target_db
            return
        tx_factory = getattr(target_db, "tx", None)
        if callable(tx_factory):
            async with tx_factory() as tx:
                yield tx
            return
        yield target_db

    async def _persist(
        self,
        item: _QueueItem,
        *,
        path: AuditIngestionPath = AuditIngestionPath.SYNC,
        repository: AuditRepository | None = None,
    ) -> None:
        target_repository = repository if repository is not None else self.repository
        organization_id = item.event.organization_id
        has_content = any(payload.has_content() for payload in item.payloads)
        repository_db = getattr(target_repository, "prisma", None)
        if organization_id and has_content and repository_db is not None:
            async with self._transaction(repository_db) as tx:
                transactional_repository = target_repository.with_db(tx)
                await self.ingestion_repository.with_db(tx).lock_content_policy(organization_id)
                content_enabled = await transactional_repository.is_content_storage_enabled_for_org(
                    organization_id
                )
                await self._persist_item_with_policy(
                    item,
                    repository=transactional_repository,
                    content_enabled=content_enabled,
                    path=path,
                )
            return

        content_enabled = (
            await self._content_storage_enabled(
                target_repository,
                organization_id,
            )
            if item.use_policy_cache
            else await target_repository.is_content_storage_enabled_for_org(organization_id)
        )
        await self._persist_item_with_policy(
            item,
            repository=target_repository,
            content_enabled=content_enabled,
            path=path,
        )

    async def _persist_item_with_policy(
        self,
        item: _QueueItem,
        *,
        repository: AuditRepository,
        content_enabled: bool,
        path: AuditIngestionPath,
    ) -> None:
        started = perf_counter()
        event_record, payload_records = _build_audit_records(
            item,
            content_enabled=content_enabled,
        )
        stored_event = await repository.create_event(event_record)

        for payload_record in payload_records:
            payload_record.event_id = stored_event.event_id
            await repository.create_payload(payload_record)
        observe_audit_ingestion_latency(path=path.value, latency_seconds=perf_counter() - started)

    async def _persist_with_bounded_retry(self, item: _QueueItem) -> None:
        attempts = self._critical_retry_attempts if item.critical else 1
        for attempt in range(1, attempts + 1):
            try:
                await self._persist(
                    item,
                    path=(
                        AuditIngestionPath.QUEUE if attempt == 1 else AuditIngestionPath.FALLBACK
                    ),
                )
                return
            except Exception:
                self.failed_events += 1
                path = AuditIngestionPath.QUEUE if attempt == 1 else AuditIngestionPath.FALLBACK
                increment_audit_write_failure(path=path.value)
                logger.exception(
                    "failed to persist audit event",
                    extra={
                        "action": item.event.action,
                        "critical": item.critical,
                        "attempt": attempt,
                    },
                )
                if attempt < attempts:
                    await asyncio.sleep(min(1.0, 0.05 * (2 ** (attempt - 1))))

    async def _content_storage_enabled(
        self,
        repository: AuditRepository,
        organization_id: str | None,
    ) -> bool:
        if not organization_id:
            return False
        now = asyncio.get_running_loop().time()
        cached = self._content_policy_cache.get(organization_id)
        if cached is not None and cached[0] > now:
            self._content_policy_cache.move_to_end(organization_id)
            return cached[1]
        if cached is not None:
            self._content_policy_cache.pop(organization_id, None)
        enabled = await repository.is_content_storage_enabled_for_org(organization_id)
        self._content_policy_cache[organization_id] = (
            now + self._content_policy_cache_ttl_seconds,
            enabled,
        )
        self._content_policy_cache.move_to_end(organization_id)
        while len(self._content_policy_cache) > self._content_policy_cache_max_entries:
            self._content_policy_cache.popitem(last=False)
        return enabled

    def invalidate_content_storage_policy(self, organization_id: str) -> None:
        self._content_policy_cache.pop(str(organization_id), None)

    async def invalidate_content_storage_policy_distributed(
        self,
        organization_id: str,
    ) -> None:
        normalized = str(organization_id)
        self.invalidate_content_storage_policy(normalized)
        enabled = False
        version = 0
        try:
            enabled, version = await self.ingestion_repository.get_content_policy(normalized)
        except Exception:
            logger.exception(
                "failed reading audit content policy for invalidation",
                extra={"organization_id": normalized},
            )
        await self.policy_invalidation.publish(
            organization_id=normalized,
            enabled=enabled,
            version=version,
        )


def _serialize_audit_item(item: _QueueItem) -> dict[str, Any]:
    return {
        "event": asdict(item.event),
        "payloads": [asdict(payload) for payload in item.payloads],
        "critical": item.critical,
    }


def _prompt_render_audit_item(event: PromptRenderEvent) -> _QueueItem:
    audit_event_id = event.audit_event_id or str(
        uuid5(NAMESPACE_URL, f"deltallm:prompt-render-audit:{event.prompt_render_log_id}")
    )
    return _QueueItem(
        event=AuditEventInput(
            action=AuditAction.PROMPT_RESOLUTION_REQUEST.value,
            organization_id=event.organization_id,
            actor_type="api_key",
            actor_id=event.user_id or event.api_key,
            api_key=event.api_key,
            resource_type="prompt",
            resource_id=event.prompt_key,
            request_id=event.request_id,
            correlation_id=event.request_id,
            ip=event.ip,
            user_agent=event.user_agent,
            status=event.status,
            latency_ms=event.latency_ms,
            error_type="PromptResolutionError" if event.error_code else None,
            error_code=event.error_code,
            metadata=dict(event.metadata or {}),
        ),
        critical=False,
        event_id=audit_event_id,
        use_policy_cache=False,
    )


def _deserialize_audit_item(payload: dict[str, Any], *, event_id: str) -> _QueueItem:
    event_data = payload.get("event")
    if not isinstance(event_data, dict):
        raise ValueError("durable audit event payload is missing event data")
    payload_items = payload.get("payloads")
    inputs = [
        AuditPayloadInput(**item)
        for item in (payload_items if isinstance(payload_items, list) else [])
        if isinstance(item, dict)
    ]
    return _QueueItem(
        event=AuditEventInput(**event_data),
        payloads=inputs,
        critical=bool(payload.get("critical", False)),
        event_id=event_id,
        use_policy_cache=False,
    )


def _redact_audit_item(item: _QueueItem) -> _QueueItem:
    payloads = [
        AuditPayloadInput(
            kind=payload.kind,
            storage_mode=payload.storage_mode,
            content_json=None,
            storage_uri=None,
            content_sha256=payload.content_sha256,
            size_bytes=payload.size_bytes,
            redacted=payload.redacted or payload.has_content(),
        )
        for payload in item.payloads
    ]
    return _QueueItem(
        event=item.event,
        payloads=payloads,
        critical=item.critical,
        event_id=item.event_id,
        use_policy_cache=item.use_policy_cache,
    )


def _shared_audit_claim_token(records: list[AuditOutboxRecord]) -> str:
    tokens = {record.claim_token for record in records}
    if len(tokens) != 1:
        raise RuntimeError("audit batch contains records from different claims")
    return tokens.pop()


def _build_audit_records(
    item: _QueueItem,
    *,
    content_enabled: bool,
) -> tuple[AuditEventRecord, list[AuditPayloadRecord]]:
    payload_records: list[AuditPayloadRecord] = []
    content_stored = False
    for payload_index, payload in enumerate(item.payloads):
        has_content = payload.has_content()
        payload_records.append(
            AuditPayloadRecord(
                payload_id=(
                    str(
                        uuid5(
                            NAMESPACE_URL,
                            f"deltallm:audit:{item.event_id}:payload:{payload_index}",
                        )
                    )
                    if item.event_id
                    else ""
                ),
                event_id=item.event_id,
                kind=payload.kind,
                storage_mode=payload.storage_mode,
                content_json=payload.content_json if content_enabled else None,
                storage_uri=payload.storage_uri if content_enabled else None,
                content_sha256=payload.content_sha256,
                size_bytes=payload.size_bytes,
                redacted=(
                    payload.redacted if content_enabled else (has_content or payload.redacted)
                ),
            )
        )
        content_stored = content_stored or (content_enabled and has_content)

    return (
        AuditEventRecord(
            event_id=item.event_id,
            action=item.event.action,
            organization_id=item.event.organization_id,
            actor_type=item.event.actor_type,
            actor_id=item.event.actor_id,
            api_key=item.event.api_key,
            resource_type=item.event.resource_type,
            resource_id=item.event.resource_id,
            request_id=item.event.request_id,
            correlation_id=item.event.correlation_id,
            ip=item.event.ip,
            user_agent=item.event.user_agent,
            status=item.event.status,
            latency_ms=item.event.latency_ms,
            input_tokens=item.event.input_tokens,
            output_tokens=item.event.output_tokens,
            error_type=item.event.error_type,
            error_code=item.event.error_code,
            metadata=item.event.metadata,
            content_stored=content_stored,
            prev_hash=item.event.prev_hash,
            event_hash=item.event.event_hash,
        ),
        payload_records,
    )


def _optional_str(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None
