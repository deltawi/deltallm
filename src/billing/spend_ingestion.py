from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
import json
import logging
from time import perf_counter
from typing import Any
from uuid import uuid4

from src.billing.fallback_gate import (
    BoundedFallbackGate,
    FallbackGateFull,
    FallbackGateTimedOut,
)
from src.billing.money import money_string
from src.billing.spend import PreparedSpendEvent, SpendTrackingService, _failure_metadata
from src.db.client import is_prisma_transaction_client
from src.db.errors import is_record_specific_database_error
from src.db.spend_ingestion import SpendIngestionRepository, SpendOutboxRecord
from src.metrics.spend_ingestion import (
    increment_spend_ingestion_cleanup,
    increment_spend_ingestion_enqueue,
    increment_spend_ingestion_failure,
    observe_spend_ingestion_batch,
    observe_spend_ingestion_ledger_rows,
    observe_spend_ingestion_latency,
    set_spend_ingestion_backlog,
    set_spend_ingestion_capacity_utilization,
    set_spend_ingestion_oldest_event_age,
    set_spend_ingestion_fallback_active,
    set_spend_ingestion_fallback_waiters,
)
from src.models.errors import ServiceUnavailableError
from src.telemetry.lifecycle import (
    WorkerHealth,
    WorkerState,
    stop_tasks_before_deadline,
    task_failure_detail,
    wait_for_startup,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SpendIngestionConfig:
    enabled: bool = False
    batch_size: int = 100
    flush_interval_seconds: float = 0.1
    lease_seconds: int = 30
    max_attempts: int = 10
    worker_enabled: bool = True
    max_pending_events: int = 100_000
    overload_policy: str = "sync_fallback"
    fallback_max_concurrency: int = 1
    fallback_max_waiters: int = 8
    fallback_queue_timeout_seconds: float = 0.1
    fallback_execution_timeout_seconds: float = 2.0
    completed_retention_hours: int = 1
    failed_retention_days: int = 30
    cleanup_interval_seconds: float = 60.0
    cleanup_batch_size: int = 1000
    cleanup_max_batches_per_run: int = 10
    cleanup_time_budget_seconds: float = 2.0
    worker_startup_timeout_seconds: float = 5.0
    shutdown_drain_timeout_seconds: float = 20.0
    worker_id: str = "gateway-spend"


_OutboxRecord = SpendOutboxRecord


class SpendIngestionOverloadedError(ServiceUnavailableError):
    error_type = "spend_ingestion_overloaded"
    message = "Spend ingestion backlog is at capacity"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message=message, code="spend_ingestion_capacity")


class SpendIngestionService:
    """Durable bounded ingress for synchronous gateway spend events."""

    def __init__(
        self,
        *,
        db_client: Any | None,
        writer: SpendTrackingService,
        config: SpendIngestionConfig,
    ) -> None:
        self.db = db_client
        self.writer = writer
        self.config = config
        self.repository = SpendIngestionRepository(db_client)
        self._running = False
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._worker_started = asyncio.Event()
        self._cleanup_started = asyncio.Event()
        self._worker_state = WorkerState.DISABLED
        self._worker_detail: str | None = None
        self._closed = False
        self._started = False
        self._fallback_gate = BoundedFallbackGate(
            concurrency=config.fallback_max_concurrency,
            max_waiters=config.fallback_max_waiters,
        )

    @property
    def durable_ingestion_enabled(self) -> bool:
        return self.config.enabled

    @property
    def worker_health(self) -> WorkerHealth:
        expected = (
            not self._closed
            and self.config.enabled
            and self.config.worker_enabled
            and self.db is not None
        )
        if not expected:
            return WorkerHealth(WorkerState.DISABLED)
        for name, task in (
            ("spend ingestion worker", self._worker),
            ("spend ingestion cleanup worker", self._cleanup_task),
        ):
            detail = task_failure_detail(task)
            if detail is not None:
                return WorkerHealth(WorkerState.FAILED, f"{name}: {detail}")
        if self._worker is None or self._cleanup_task is None:
            return WorkerHealth(WorkerState.FAILED, "expected worker task is missing")
        return WorkerHealth(self._worker_state, self._worker_detail)

    def with_db(self, db_client: Any | None) -> SpendTrackingService:
        # Transactional batch completion processing must remain synchronous
        # with its own completion-outbox acknowledgement.
        return self.writer.with_db(db_client)

    async def start(self) -> None:
        if self._started:
            if self.worker_health.ready:
                return
            raise RuntimeError("spend ingestion service was started but is not healthy")
        self._closed = False
        if not self.config.enabled:
            self._worker_state = WorkerState.DISABLED
            self._worker_detail = None
            self._started = True
            return
        if self.db is None:
            self._worker_state = WorkerState.FAILED
            self._worker_detail = "spend outbox mode requires the telemetry database pool"
            raise RuntimeError("spend outbox mode requires the telemetry database pool")
        try:
            await asyncio.wait_for(
                self.repository.reconcile_capacity(),
                timeout=self.config.worker_startup_timeout_seconds,
            )
        except Exception as exc:
            self._worker_state = WorkerState.FAILED
            self._worker_detail = f"capacity reconciliation failed: {type(exc).__name__}: {exc}"
            increment_spend_ingestion_failure("capacity_reconcile")
            logger.exception("failed to reconcile spend ingestion capacity")
            raise
        if not self.config.worker_enabled:
            self._worker_state = WorkerState.DISABLED
            self._worker_detail = None
            self._started = True
            return
        if self._worker is not None and not self._worker.done():
            return
        self._worker_state = WorkerState.STARTING
        self._worker_detail = None
        self._worker_started.clear()
        self._cleanup_started.clear()
        self._running = True
        self._worker = asyncio.create_task(self._worker_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        startup_deadline = (
            asyncio.get_running_loop().time() + self.config.worker_startup_timeout_seconds
        )
        try:
            await wait_for_startup(
                started=self._worker_started,
                task=self._worker,
                timeout_seconds=max(0.0, startup_deadline - asyncio.get_running_loop().time()),
                worker_name="spend ingestion worker",
            )
            await wait_for_startup(
                started=self._cleanup_started,
                task=self._cleanup_task,
                timeout_seconds=max(0.0, startup_deadline - asyncio.get_running_loop().time()),
                worker_name="spend ingestion cleanup worker",
            )
        except Exception as exc:
            self._running = False
            self._worker_state = WorkerState.FAILED
            self._worker_detail = f"{type(exc).__name__}: {exc}"
            self._wake.set()
            await stop_tasks_before_deadline(
                [self._worker, self._cleanup_task],
                deadline=asyncio.get_running_loop().time(),
                cancel_first=True,
            )
            raise
        self._worker_state = WorkerState.READY
        self._started = True

    async def shutdown(self) -> None:
        self._closed = True
        await self._stop_worker_tasks(drain_pending=True)
        self._started = False

    async def _stop_worker_tasks(self, *, drain_pending: bool) -> None:
        if self._worker is None and self._cleanup_task is None:
            self._running = False
            self._worker_state = WorkerState.DISABLED
            self._worker_detail = None
            return
        self._worker_state = WorkerState.STOPPING
        self._worker_detail = None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.shutdown_drain_timeout_seconds
        if drain_pending:
            while loop.time() < deadline:
                pending_count = await self._pending_count_before_deadline(deadline)
                if pending_count == 0:
                    break
                if pending_count is None:
                    increment_spend_ingestion_failure("shutdown_pending_probe_timeout")
                    break
                self._wake.set()
                await asyncio.sleep(
                    min(
                        0.05,
                        self.config.flush_interval_seconds,
                        max(0.0, deadline - loop.time()),
                    )
                )
        self._running = False
        self._wake.set()
        cleanup_stopped = await stop_tasks_before_deadline(
            [self._cleanup_task],
            deadline=deadline,
            cancel_first=True,
        )
        worker_stopped = await stop_tasks_before_deadline(
            [self._worker],
            deadline=deadline,
        )
        if not cleanup_stopped or not worker_stopped:
            increment_spend_ingestion_failure("shutdown_timeout")
            logger.error("spend ingestion worker exceeded its shutdown deadline and was cancelled")
        self._cleanup_task = None
        self._worker = None
        self._worker_state = WorkerState.DISABLED
        self._worker_detail = None

    async def reconfigure(self, config: SpendIngestionConfig) -> None:
        if config.enabled != self.config.enabled:
            raise RuntimeError("changing spend ingestion mode requires a restart")
        previous = self.config
        worker_active = self._worker is not None and not self._worker.done()
        worker_desired = config.enabled and config.worker_enabled and self.db is not None
        if worker_active and not worker_desired:
            await self._stop_worker_tasks(drain_pending=not config.enabled)
        self.config = config
        await self._fallback_gate.reconfigure(
            concurrency=config.fallback_max_concurrency,
            max_waiters=config.fallback_max_waiters,
        )
        if worker_desired and not worker_active:
            self._started = False
            try:
                await self.start()
            except Exception:
                self.config = previous
                self._started = True
                await self._fallback_gate.reconfigure(
                    concurrency=previous.fallback_max_concurrency,
                    max_waiters=previous.fallback_max_waiters,
                )
                raise

    async def log_spend(self, **kwargs: Any) -> None:
        event_id = kwargs.pop("event_id", None)
        if not self.config.enabled:
            await self.writer.log_spend(**kwargs)
            return
        await self._enqueue("spend", kwargs, event_id=event_id)

    async def log_request_failure(self, **kwargs: Any) -> None:
        event_id = kwargs.pop("event_id", None)
        if not self.config.enabled:
            await self.writer.log_request_failure(**kwargs)
            return
        payload = dict(kwargs)
        exc = payload.pop("exc", None)
        payload["metadata"] = _failure_metadata(
            metadata=payload.get("metadata"),
            exc=exc,
            http_status_code=payload.get("http_status_code"),
        )
        payload["error_type"] = (
            payload.get("error_type")
            or getattr(exc, "error_type", None)
            or (exc.__class__.__name__ if exc is not None else None)
        )
        await self._enqueue("request_failure", payload, event_id=event_id)

    async def log_spend_once(self, **kwargs: Any) -> Any:
        return await self.writer.log_spend_once(**kwargs)

    async def _enqueue(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> None:
        if self.db is None:
            raise RuntimeError("spend ingestion database is unavailable")
        accepted_payload = dict(payload)
        if event_type == "spend":
            accepted_payload["cost_exact"] = money_string(payload.get("cost"))
            accepted_payload["spend_event_version"] = 2
        # This identifier is owned by the server and is intentionally unrelated
        # to the caller-controlled request ID. It is persisted once and reused
        # by the outbox worker and synchronous overload fallback.
        accepted_event_id = str(event_id or uuid4())
        result = await self.repository.enqueue(
            event_id=accepted_event_id,
            event_type=event_type,
            payload=json.loads(json.dumps(accepted_payload, default=_json_default)),
            max_attempts=self.config.max_attempts,
            max_pending_events=self.config.max_pending_events,
        )
        increment_spend_ingestion_enqueue(result.status)
        set_spend_ingestion_backlog(result.pending_count)
        set_spend_ingestion_capacity_utilization(
            pending=result.pending_count,
            capacity=self.config.max_pending_events,
        )
        if result.status == "full":
            await self._handle_overload(
                event_id=accepted_event_id,
                event_type=event_type,
                payload=accepted_payload,
            )
            return
        self._wake.set()

    async def _handle_overload(
        self,
        *,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if self.config.overload_policy != "sync_fallback":
            raise SpendIngestionOverloadedError("spend ingestion backlog is at capacity")
        try:
            await self._fallback_gate.acquire(
                timeout_seconds=self.config.fallback_queue_timeout_seconds
            )
        except FallbackGateFull as exc:
            increment_spend_ingestion_enqueue("sync_fallback_waiters_full")
            self._publish_fallback_capacity()
            raise SpendIngestionOverloadedError(str(exc)) from exc
        except FallbackGateTimedOut as exc:
            increment_spend_ingestion_enqueue("sync_fallback_queue_timeout")
            self._publish_fallback_capacity()
            raise SpendIngestionOverloadedError(str(exc)) from exc
        self._publish_fallback_capacity()
        try:
            increment_spend_ingestion_enqueue("sync_fallback")
            try:
                async with asyncio.timeout(self.config.fallback_execution_timeout_seconds):
                    async with self._transaction() as tx:
                        await self.writer.with_db(tx).log_batch_once(
                            [(event_id, event_type, payload)]
                        )
            except TimeoutError as exc:
                increment_spend_ingestion_enqueue("sync_fallback_execution_timeout")
                raise SpendIngestionOverloadedError(
                    "synchronous spend fallback execution deadline exceeded"
                ) from exc
        finally:
            await self._fallback_gate.release()
            self._publish_fallback_capacity()

    def _publish_fallback_capacity(self) -> None:
        set_spend_ingestion_fallback_active(self._fallback_gate.active)
        set_spend_ingestion_fallback_waiters(self._fallback_gate.waiters)

    async def _worker_loop(self) -> None:
        self._worker_started.set()
        consecutive_failures = 0
        while self._running:
            try:
                await self._worker_iteration()
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
                increment_spend_ingestion_failure("worker_iteration")
                logger.exception("spend ingestion worker iteration failed; continuing")
                await asyncio.sleep(min(5.0, 0.1 * (2 ** min(consecutive_failures - 1, 6))))

    async def _worker_iteration(self) -> None:
        try:
            records = await self._claim_batch()
        except Exception:
            increment_spend_ingestion_failure("claim")
            raise
        if not records:
            self._wake.clear()
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=self.config.flush_interval_seconds,
                )
            except TimeoutError:
                pass
            return
        started = perf_counter()
        await self._process_batch(records)
        observe_spend_ingestion_batch(len(records))
        observe_spend_ingestion_latency(perf_counter() - started)
        try:
            await self._publish_backlog()
        except Exception:
            increment_spend_ingestion_failure("backlog_metrics")
            logger.debug("failed to publish spend ingestion backlog", exc_info=True)

    async def _claim_batch(self) -> list[_OutboxRecord]:
        return await self.repository.claim_batch(
            limit=self.config.batch_size,
            worker_id=self.config.worker_id,
            claim_token=str(uuid4()),
            lease_seconds=self.config.lease_seconds,
        )

    async def _process(self, record: _OutboxRecord) -> None:
        await self._process_batch([record])

    async def _process_batch(self, records: list[_OutboxRecord]) -> None:
        valid: list[tuple[_OutboxRecord, PreparedSpendEvent]] = []
        for record in records:
            try:
                payload = _restore_datetimes(record.payload)
                prepared = self.writer.prepare_batch_event(
                    event_id=record.event_id,
                    event_type=record.event_type,
                    payload=payload,
                )
            except Exception as exc:
                increment_spend_ingestion_failure("validation")
                await self._safe_mark_retry(record, exc)
            else:
                valid.append((record, prepared))
        if not valid:
            return
        await self._commit_batch_with_isolation(valid)

    async def _commit_batch_with_isolation(
        self,
        records: list[tuple[_OutboxRecord, PreparedSpendEvent]],
    ) -> None:
        try:
            ledger_counts = await self._commit_prepared_batch(records)
        except Exception as exc:
            increment_spend_ingestion_failure("processing")
            if len(records) > 1 and is_record_specific_database_error(exc):
                midpoint = len(records) // 2
                await self._commit_batch_with_isolation(records[:midpoint])
                await self._commit_batch_with_isolation(records[midpoint:])
                return
            for record, _ in records:
                await self._safe_mark_retry(record, exc)
            return
        for entity_type, count in ledger_counts.items():
            observe_spend_ingestion_ledger_rows(entity_type=entity_type, value=count)

    async def _commit_prepared_batch(
        self,
        records: list[tuple[_OutboxRecord, PreparedSpendEvent]],
    ) -> dict[str, int]:
        claim_token = _shared_claim_token([record for record, _ in records])
        heartbeat = asyncio.create_task(
            self._lease_heartbeat(
                event_ids=[record.event_id for record, _ in records],
                claim_token=claim_token,
            )
        )
        try:
            async with self._transaction() as tx:
                batch_writer = self.writer.with_db(tx)
                _, ledger_counts = await batch_writer.log_prepared_batch_once(
                    [prepared for _, prepared in records]
                )
                completed = await self.repository.with_db(tx).mark_completed(
                    event_ids=[record.event_id for record, _ in records],
                    worker_id=self.config.worker_id,
                    claim_token=claim_token,
                )
                if completed != len(records):
                    raise RuntimeError(
                        f"spend batch lease ownership changed: completed={completed} "
                        f"expected={len(records)}"
                    )
            return ledger_counts
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _lease_heartbeat(self, *, event_ids: list[str], claim_token: str) -> None:
        interval = max(0.1, self.config.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await self.repository.renew_lease(
                    event_ids=event_ids,
                    worker_id=self.config.worker_id,
                    claim_token=claim_token,
                    lease_seconds=self.config.lease_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                increment_spend_ingestion_failure("lease_heartbeat")
                logger.exception("failed renewing spend ingestion lease")
                continue
            if renewed != len(event_ids):
                increment_spend_ingestion_failure("lease_lost")
                logger.warning(
                    "spend ingestion lease ownership changed during processing",
                    extra={"expected": len(event_ids), "renewed": renewed},
                )
                return

    async def _safe_mark_retry(self, record: _OutboxRecord, exc: Exception) -> None:
        try:
            await self._mark_retry(record, exc)
        except asyncio.CancelledError:
            raise
        except Exception:
            increment_spend_ingestion_failure("retry_transition")
            logger.exception(
                "failed to transition spend ingestion event for retry; lease will expire",
                extra={"event_id": record.event_id},
            )

    async def _mark_retry(self, record: _OutboxRecord, exc: Exception) -> None:
        terminal = await self.repository.mark_retry(
            record=record,
            worker_id=self.config.worker_id,
            error=str(exc),
        )
        logger.error(
            "spend ingestion event failed",
            extra={
                "event_id": record.event_id,
                "attempt": record.attempt_count,
                "terminal": terminal,
            },
        )

    async def _pending_count(self) -> int:
        return await self.repository.drainable_count()

    async def _pending_count_before_deadline(self, deadline: float) -> int | None:
        task = asyncio.create_task(self._pending_count())
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        done, _ = await asyncio.wait({task}, timeout=remaining)
        if task in done:
            return task.result()
        await stop_tasks_before_deadline(
            [task],
            deadline=deadline,
            cancel_first=True,
        )
        return None

    async def _publish_backlog(self) -> None:
        count, oldest_age = await self.repository.pending_stats()
        set_spend_ingestion_backlog(count)
        set_spend_ingestion_oldest_event_age(oldest_age)
        set_spend_ingestion_capacity_utilization(
            pending=count,
            capacity=self.config.max_pending_events,
        )

    async def _cleanup_loop(self) -> None:
        self._cleanup_started.set()
        while self._running:
            await asyncio.sleep(max(0.1, self.config.cleanup_interval_seconds))
            if self._running:
                await self._cleanup_terminal()

    async def _cleanup_terminal(self) -> int:
        started = perf_counter()
        deleted_total = 0
        for _ in range(max(1, self.config.cleanup_max_batches_per_run)):
            if perf_counter() - started >= self.config.cleanup_time_budget_seconds:
                break
            try:
                deleted = await self.repository.cleanup_terminal(
                    completed_retention_hours=self.config.completed_retention_hours,
                    limit=self.config.cleanup_batch_size,
                )
            except Exception:
                increment_spend_ingestion_failure("cleanup")
                logger.exception("failed cleaning spend ingestion outbox")
                break
            deleted_total += deleted
            increment_spend_ingestion_cleanup(deleted)
            if deleted < self.config.cleanup_batch_size:
                break
        return deleted_total

    @asynccontextmanager
    async def _transaction(self):  # noqa: ANN202
        if self.db is None:
            raise RuntimeError("spend ingestion database is unavailable")
        if is_prisma_transaction_client(self.db):
            yield self.db
            return
        tx_factory = getattr(self.db, "tx", None)
        if callable(tx_factory):
            async with tx_factory() as tx:
                yield tx
            return
        yield self.db


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _restore_datetimes(payload: dict[str, Any]) -> dict[str, Any]:
    restored = dict(payload)
    for key in ("start_time", "end_time"):
        value = restored.get(key)
        if isinstance(value, str):
            restored[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return restored


def _shared_claim_token(records: list[_OutboxRecord]) -> str:
    tokens = {record.claim_token for record in records}
    if len(tokens) != 1:
        raise RuntimeError("spend batch contains records from different claims")
    return tokens.pop()
