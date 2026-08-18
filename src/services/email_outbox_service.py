from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import logging
from time import perf_counter
from typing import Any, Iterable
from uuid import NAMESPACE_URL, uuid4, uuid5

from src.audit.actions import AuditAction
from src.audit.delivery import AuditDeliveryClass
from src.db.email import EmailOutboxRecord, EmailOutboxRepository
from src.email.models import (
    EmailConfigurationError,
    EmailDeliveryDisposition,
    EmailDeliveryError,
    PreparedEmail,
)
from src.metrics import (
    increment_email_delivery_attempt,
    increment_email_delivery_unknown,
    increment_email_worker_failure,
    observe_email_delivery_latency,
    set_email_delivery_audit_backlog,
    set_email_queue_depth,
)
from src.services.audit_service import AuditEventInput, AuditService, enqueue_audit_event
from src.services.email_delivery_service import EmailDeliveryService
from src.telemetry.lifecycle import (
    WorkerHealth,
    WorkerState,
    stop_tasks_before_deadline,
    task_failure_detail,
    wait_for_startup,
)

logger = logging.getLogger(__name__)
SUPPRESSED_RECIPIENTS_METADATA_KEY = "suppressed_recipients"


def _normalize_address(value: str) -> str:
    return str(value or "").strip().lower()


def enqueue_succeeded(record: Any) -> bool:
    return str(getattr(record, "status", "") or "").strip().lower() == "queued"


def _has_any_recipients(prepared: PreparedEmail) -> bool:
    return bool(prepared.to_addresses or prepared.cc_addresses or prepared.bcc_addresses)


def _apply_suppressed_recipients(prepared: PreparedEmail, *, suppressed: set[str]) -> PreparedEmail:
    if not suppressed:
        return prepared

    payload = dict(prepared.payload_json or {})
    existing = payload.get(SUPPRESSED_RECIPIENTS_METADATA_KEY)
    existing_values = (
        {_normalize_address(item) for item in existing if _normalize_address(item)}
        if isinstance(existing, list)
        else set()
    )
    payload[SUPPRESSED_RECIPIENTS_METADATA_KEY] = sorted(existing_values | suppressed)

    def _allowed(addresses: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            address for address in addresses if _normalize_address(address) not in suppressed
        )

    return replace(
        prepared,
        to_addresses=_allowed(prepared.to_addresses),
        cc_addresses=_allowed(prepared.cc_addresses),
        bcc_addresses=_allowed(prepared.bcc_addresses),
        payload_json=payload,
    )


async def _filter_suppressed_prepared_email(
    *,
    prepared: PreparedEmail,
    feedback_repository,  # noqa: ANN001
) -> tuple[PreparedEmail, set[str]]:
    if feedback_repository is None:
        return prepared, set()
    suppressed = await feedback_repository.get_suppressed_addresses(
        [
            *prepared.to_addresses,
            *prepared.cc_addresses,
            *prepared.bcc_addresses,
        ]
    )
    normalized = {
        _normalize_address(address) for address in suppressed if _normalize_address(address)
    }
    if not normalized:
        return prepared, set()
    return _apply_suppressed_recipients(prepared, suppressed=normalized), normalized


@dataclass
class EmailWorkerConfig:
    poll_interval_seconds: float = 5.0
    max_batch_size: int = 10
    max_concurrency: int = 3
    delivery_lease_seconds: int = 60
    audit_lease_seconds: int = 30
    startup_timeout_seconds: float = 5.0
    shutdown_drain_timeout_seconds: float = 20.0


class EmailOutboxService:
    def __init__(
        self,
        *,
        repository: EmailOutboxRepository,
        delivery_service: EmailDeliveryService,
        config_getter,
        feedback_repository=None,
    ) -> None:  # noqa: ANN001
        self.repository = repository
        self.delivery_service = delivery_service
        self._config_getter = config_getter
        self.feedback_repository = feedback_repository

    def with_repository(
        self, repository: EmailOutboxRepository, *, feedback_repository=None
    ) -> EmailOutboxService:  # noqa: ANN001
        return EmailOutboxService(
            repository=repository,
            delivery_service=self.delivery_service,
            config_getter=self._config_getter,
            feedback_repository=self.feedback_repository
            if feedback_repository is None
            else feedback_repository,
        )

    async def enqueue_template_email(
        self,
        *,
        template_key: str,
        to_addresses: Iterable[str],
        payload_json: dict[str, Any] | None = None,
        kind: str = "transactional",
        provider_override: str | None = None,
        created_by_account_id: str | None = None,
    ) -> EmailOutboxRecord:
        prepared = self.delivery_service.prepare_template_email(
            template_key=template_key,
            to_addresses=to_addresses,
            payload_json=payload_json,
            kind=kind,
            provider_override=provider_override,
        )
        prepared = await self._filter_suppressed_recipients(prepared)
        email_id = str(uuid4())
        requires_delivery_audit = prepared.kind == "test"
        status = "cancelled" if not prepared.to_addresses else "queued"
        record = EmailOutboxRecord(
            email_id=email_id,
            kind=prepared.kind,
            provider=prepared.provider,
            to_addresses=list(prepared.to_addresses),
            cc_addresses=list(prepared.cc_addresses),
            bcc_addresses=list(prepared.bcc_addresses),
            from_address=prepared.from_address,
            reply_to=prepared.reply_to,
            template_key=prepared.template_key,
            payload_json=prepared.payload_json,
            subject=prepared.subject,
            text_body=prepared.text_body,
            html_body=prepared.html_body,
            status=status,
            max_attempts=self._max_attempts(),
            next_attempt_at=datetime.now(tz=UTC),
            created_by_account_id=created_by_account_id,
            last_error=None if prepared.to_addresses else "all recipients are suppressed",
            delivery_audit_status=(
                "pending"
                if requires_delivery_audit and status == "cancelled"
                else "waiting"
                if requires_delivery_audit
                else "not_required"
            ),
            delivery_audit_event_id=(
                str(uuid5(NAMESPACE_URL, f"deltallm:email-delivery:{email_id}"))
                if requires_delivery_audit
                else None
            ),
            delivery_audit_next_attempt_at=(
                datetime.now(tz=UTC) if requires_delivery_audit and status == "cancelled" else None
            ),
        )
        stored = await self.repository.enqueue(record)
        await self._refresh_queue_depth()
        return stored

    async def _refresh_queue_depth(self) -> None:
        set_email_queue_depth(await self.repository.count_pending())

    def _max_attempts(self) -> int:
        cfg = self._config_getter()
        general = getattr(cfg, "general_settings", None)
        return int(getattr(general, "email_max_attempts", 5) or 5)

    async def _filter_suppressed_recipients(self, prepared: PreparedEmail) -> PreparedEmail:
        filtered, suppressed = await _filter_suppressed_prepared_email(
            prepared=prepared,
            feedback_repository=self.feedback_repository,
        )
        if not suppressed:
            return prepared
        logger.info(
            "suppressed email recipients removed before enqueue",
            extra={
                "template_key": prepared.template_key,
                "provider": prepared.provider,
                "suppressed_recipient_count": len(suppressed),
            },
        )
        return filtered


class EmailOutboxWorker:
    def __init__(
        self,
        *,
        repository: EmailOutboxRepository,
        delivery_service: EmailDeliveryService,
        config_getter,
        audit_service: AuditService | None = None,
        config: EmailWorkerConfig | None = None,
        feedback_repository=None,
    ) -> None:  # noqa: ANN001
        self.repository = repository
        self.delivery_service = delivery_service
        self._config_getter = config_getter
        self.audit_service = audit_service
        self.config = config or EmailWorkerConfig()
        self.feedback_repository = feedback_repository
        self._stopped = False
        self._worker_id = f"email-worker-{uuid4()}"
        self._task: asyncio.Task[None] | None = None
        self._started = asyncio.Event()
        self._state = WorkerState.DISABLED
        self._detail: str | None = None
        self._blocked_audits = 0

    @property
    def worker_health(self) -> WorkerHealth:
        if self._stopped:
            return WorkerHealth(WorkerState.DISABLED)
        failure = task_failure_detail(self._task)
        if failure is not None:
            return WorkerHealth(WorkerState.FAILED, failure)
        if self._task is None:
            return WorkerHealth(WorkerState.FAILED, "expected email worker task is missing")
        if self._blocked_audits:
            return WorkerHealth(
                WorkerState.FAILED,
                f"{self._blocked_audits} required email delivery audit record(s) blocked",
            )
        return WorkerHealth(self._state, self._detail)

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._started.clear()
        self._state = WorkerState.STARTING
        self._detail = None
        self._task = asyncio.create_task(self.run())
        try:
            await wait_for_startup(
                started=self._started,
                task=self._task,
                timeout_seconds=self.config.startup_timeout_seconds,
                worker_name="email outbox worker",
            )
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                self._state = WorkerState.STOPPING
                self._detail = "startup cancelled"
            else:
                self._state = WorkerState.FAILED
                self._detail = f"{type(exc).__name__}: {exc}"
            await stop_tasks_before_deadline(
                [self._task],
                deadline=(
                    asyncio.get_running_loop().time()
                    + min(1.0, self.config.shutdown_drain_timeout_seconds)
                ),
                cancel_first=True,
            )
            raise
        self._state = WorkerState.READY

    def stop(self) -> None:
        self._stopped = True

    async def shutdown(self) -> None:
        self._stopped = True
        self._state = WorkerState.STOPPING
        deadline = asyncio.get_running_loop().time() + self.config.shutdown_drain_timeout_seconds
        stopped = await stop_tasks_before_deadline([self._task], deadline=deadline)
        if not stopped:
            increment_email_worker_failure(phase="shutdown_timeout")
            logger.error("email worker exceeded shutdown deadline and was cancelled")
        self._task = None
        self._state = WorkerState.DISABLED
        self._detail = None

    async def run(self) -> None:
        consecutive_failures = 0
        while not self._stopped:
            try:
                processed = await self.process_once()
                consecutive_failures = 0
                self._state = WorkerState.READY
                self._detail = None
                self._started.set()
                if processed == 0:
                    await asyncio.sleep(self.config.poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                consecutive_failures += 1
                self._state = (
                    WorkerState.FAILED if consecutive_failures >= 3 else WorkerState.DEGRADED
                )
                self._detail = f"worker iteration failed {consecutive_failures} consecutive time(s)"
                increment_email_worker_failure(phase="iteration")
                logger.exception("email worker iteration failed; continuing")
                await asyncio.sleep(min(5.0, 0.1 * (2 ** min(consecutive_failures - 1, 6))))

    async def process_once(self) -> int:
        recovered = await self.repository.recover_expired_delivery_claims(
            limit=self.config.max_batch_size
        )
        claimed = await self.repository.claim_due(
            limit=self.config.max_batch_size,
            worker_id=self._worker_id,
            claim_token=str(uuid4()),
            lease_seconds=self.config.delivery_lease_seconds,
        )
        await self._run_bounded(claimed, self._process_record)

        audit_claims = await self.repository.claim_due_delivery_audits(
            limit=self.config.max_batch_size,
            worker_id=self._worker_id,
            lease_seconds=self.config.audit_lease_seconds,
            claim_token=str(uuid4()),
        )
        await self._run_bounded(audit_claims, self._process_delivery_audit)

        set_email_queue_depth(await self.repository.count_pending())
        audit_counts = await self.repository.count_delivery_audits_by_status()
        self._blocked_audits = int(audit_counts.get("blocked", 0))
        set_email_delivery_audit_backlog(audit_counts)
        return recovered + len(claimed) + len(audit_claims)

    async def _run_bounded(self, records: list[EmailOutboxRecord], handler) -> None:  # noqa: ANN001
        if not records:
            return
        semaphore = asyncio.Semaphore(max(1, min(self.config.max_concurrency, len(records))))

        async def _run(record: EmailOutboxRecord) -> None:
            async with semaphore:
                try:
                    await handler(record)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    increment_email_worker_failure(phase="record")
                    logger.exception(
                        "email worker record failed; continuing batch",
                        extra={"email_id": record.email_id},
                    )

        await asyncio.gather(*[_run(record) for record in records])

    async def _process_record(self, record: EmailOutboxRecord) -> None:
        started = perf_counter()
        stop_renewal = asyncio.Event()
        lease_lost = asyncio.Event()
        renewal_task = asyncio.create_task(
            self._maintain_delivery_lease(record, stop_renewal, lease_lost)
        )
        try:
            await self._deliver_record(record, lease_lost=lease_lost)
        finally:
            stop_renewal.set()
            with suppress(asyncio.CancelledError):
                await renewal_task
            self._observe_delivery_latency(record=record, started=started)

    async def _maintain_delivery_lease(
        self,
        record: EmailOutboxRecord,
        stop: asyncio.Event,
        lease_lost: asyncio.Event,
    ) -> None:
        claim_token = record.delivery_claim_token
        if not claim_token:
            lease_lost.set()
            return
        interval = max(1.0, self.config.delivery_lease_seconds / 3)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                pass
            try:
                renewed = await self.repository.renew_delivery_claim(
                    email_id=record.email_id,
                    worker_id=self._worker_id,
                    claim_token=claim_token,
                    lease_seconds=self.config.delivery_lease_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "email delivery lease renewal failed",
                    extra={"email_id": record.email_id},
                )
                lease_lost.set()
                return
            if not renewed:
                lease_lost.set()
                return

    async def _deliver_record(
        self, record: EmailOutboxRecord, *, lease_lost: asyncio.Event
    ) -> None:
        claim_token = record.delivery_claim_token
        if not claim_token:
            logger.error(
                "email delivery claim has no fencing token",
                extra={"email_id": record.email_id},
            )
            return
        prepared = PreparedEmail(
            kind=record.kind,  # type: ignore[arg-type]
            provider=record.provider,  # type: ignore[arg-type]
            to_addresses=tuple(record.to_addresses),
            cc_addresses=tuple(record.cc_addresses),
            bcc_addresses=tuple(record.bcc_addresses),
            from_address=record.from_address,
            reply_to=record.reply_to,
            template_key=record.template_key,
            payload_json=record.payload_json,
            subject=record.subject,
            text_body=record.text_body,
            html_body=record.html_body,
        )
        try:
            prepared = await self._reapply_suppressions(record=record, prepared=prepared)
            if not _has_any_recipients(prepared):
                reason = "all recipients are suppressed"
                transitioned = await self.repository.cancel(
                    record.email_id,
                    worker_id=self._worker_id,
                    claim_token=claim_token,
                    reason=reason,
                    delivery_audit_event_id=self._delivery_audit_event_id(record),
                )
                if not transitioned:
                    logger.warning(
                        "email cancellation ignored because delivery claim was lost",
                        extra={"email_id": record.email_id},
                    )
                    return
                increment_email_delivery_attempt(
                    provider=record.provider, kind=record.kind, status="cancelled"
                )
                self._log_delivery(record=record, status="cancelled", error=reason)
                return
            if lease_lost.is_set():
                return
            started = await self.repository.begin_delivery_attempt(
                email_id=record.email_id,
                worker_id=self._worker_id,
                claim_token=claim_token,
            )
            if not started:
                logger.warning(
                    "email delivery claim was lost before provider attempt",
                    extra={"email_id": record.email_id},
                )
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            retry_at = datetime.now(tz=UTC) + timedelta(
                seconds=self._retry_delay_seconds(record.attempt_count + 1)
            )
            try:
                await self.repository.release_delivery_claim(
                    email_id=record.email_id,
                    worker_id=self._worker_id,
                    claim_token=claim_token,
                    error=str(exc),
                    next_attempt_at=retry_at,
                )
            except Exception:
                logger.exception(
                    "email pre-delivery claim release failed; lease recovery will retry",
                    extra={"email_id": record.email_id},
                )
            return

        active_record = replace(
            record,
            status="sending",
            attempt_count=record.attempt_count + 1,
            delivery_started_at=datetime.now(tz=UTC),
        )
        try:
            result = await self.delivery_service.send_prepared_email(prepared)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive integration boundary
            await self._record_delivery_failure(
                record=active_record,
                claim_token=claim_token,
                exc=exc,
            )
            return

        try:
            transitioned = await self.repository.mark_sent(
                active_record.email_id,
                worker_id=self._worker_id,
                claim_token=claim_token,
                provider_message_id=result.provider_message_id,
                delivery_audit_event_id=self._delivery_audit_event_id(active_record),
            )
            if not transitioned:
                increment_email_delivery_unknown(
                    provider=record.provider,
                    kind=record.kind,
                    reason="claim_lost_after_success",
                )
                logger.error(
                    "email provider succeeded after delivery claim was lost; refusing to resend",
                    extra={"email_id": record.email_id, "provider": record.provider},
                )
                return
            increment_email_delivery_attempt(
                provider=record.provider, kind=record.kind, status="sent"
            )
            self._log_delivery(
                record=record, status="sent", provider_message_id=result.provider_message_id
            )
        except Exception:
            increment_email_delivery_unknown(
                provider=record.provider,
                kind=record.kind,
                reason="terminal_persist_failed",
            )
            logger.exception(
                "email provider succeeded but terminal delivery state could not be persisted; refusing to resend",
                extra={"email_id": record.email_id, "provider": record.provider},
            )
            return

    @staticmethod
    def _observe_delivery_latency(*, record: EmailOutboxRecord, started: float) -> None:
        observe_email_delivery_latency(
            provider=record.provider,
            kind=record.kind,
            latency_seconds=perf_counter() - started,
        )

    async def _record_delivery_failure(
        self,
        *,
        record: EmailOutboxRecord,
        claim_token: str,
        exc: Exception,
    ) -> None:
        error = str(exc)
        if not isinstance(exc, (EmailConfigurationError, EmailDeliveryError)) or (
            isinstance(exc, EmailDeliveryError)
            and exc.disposition is EmailDeliveryDisposition.OUTCOME_UNKNOWN
        ):
            try:
                transitioned = await self.repository.mark_delivery_unknown(
                    record.email_id,
                    worker_id=self._worker_id,
                    claim_token=claim_token,
                    error=error,
                    delivery_audit_event_id=self._delivery_audit_event_id(record),
                )
            except Exception:
                logger.exception(
                    "ambiguous email delivery state could not be persisted; lease recovery will block it",
                    extra={"email_id": record.email_id},
                )
                return
            if transitioned:
                increment_email_delivery_unknown(
                    provider=record.provider,
                    kind=record.kind,
                    reason="provider_outcome_unknown",
                )
                self._log_delivery(record=record, status="delivery_unknown", error=error)
            return
        should_retry = self._should_retry(record=record, exc=exc)
        if not should_retry:
            transitioned = await self.repository.mark_failed(
                record.email_id,
                worker_id=self._worker_id,
                claim_token=claim_token,
                error=error,
                delivery_audit_event_id=self._delivery_audit_event_id(record),
            )
            if transitioned:
                increment_email_delivery_attempt(
                    provider=record.provider, kind=record.kind, status="failed"
                )
                self._log_delivery(record=record, status="failed", error=error)
            return

        retry_delay_seconds = self._retry_delay_seconds(record.attempt_count)
        transitioned = await self.repository.mark_retry(
            record.email_id,
            worker_id=self._worker_id,
            claim_token=claim_token,
            error=error,
            next_attempt_at=datetime.now(tz=UTC) + timedelta(seconds=retry_delay_seconds),
        )
        if transitioned:
            increment_email_delivery_attempt(
                provider=record.provider, kind=record.kind, status="retrying"
            )
            self._log_delivery(
                record=record,
                status="retrying",
                error=error,
                retry_delay_seconds=retry_delay_seconds,
            )

    def _should_retry(self, *, record: EmailOutboxRecord, exc: Exception) -> bool:
        if isinstance(exc, EmailConfigurationError):
            return False
        if isinstance(exc, EmailDeliveryError) and not exc.retriable:
            return False
        return record.attempt_count < record.max_attempts

    def _retry_delay_seconds(self, attempt_count: int) -> int:
        cfg = self._config_getter()
        general = getattr(cfg, "general_settings", None)
        initial = int(getattr(general, "email_retry_initial_seconds", 60) or 60)
        max_delay = int(getattr(general, "email_retry_max_seconds", 3600) or 3600)
        return min(initial * max(1, 2 ** max(0, attempt_count - 1)), max_delay)

    async def _reapply_suppressions(
        self, *, record: EmailOutboxRecord, prepared: PreparedEmail
    ) -> PreparedEmail:
        filtered, suppressed = await _filter_suppressed_prepared_email(
            prepared=prepared,
            feedback_repository=self.feedback_repository,
        )
        if not suppressed:
            return prepared
        await self.repository.update_recipients_and_payload(
            record.email_id,
            to_addresses=list(filtered.to_addresses),
            cc_addresses=list(filtered.cc_addresses),
            bcc_addresses=list(filtered.bcc_addresses),
            payload_json=filtered.payload_json,
            worker_id=self._worker_id,
            claim_token=record.delivery_claim_token or "",
        )
        logger.info(
            "suppressed email recipients removed before send",
            extra={
                "email_id": record.email_id,
                "template_key": record.template_key,
                "provider": record.provider,
                "suppressed_recipient_count": len(suppressed),
            },
        )
        return filtered

    def _log_delivery(
        self,
        *,
        record: EmailOutboxRecord,
        status: str,
        error: str | None = None,
        provider_message_id: str | None = None,
        retry_delay_seconds: int | None = None,
    ) -> None:
        extra = {
            "email_id": record.email_id,
            "kind": record.kind,
            "provider": record.provider,
            "status": status,
            "attempt_count": record.attempt_count,
            "max_attempts": record.max_attempts,
            "template_key": record.template_key,
        }
        if provider_message_id:
            extra["provider_message_id"] = provider_message_id
        if retry_delay_seconds is not None:
            extra["retry_delay_seconds"] = retry_delay_seconds
        if error:
            extra["error"] = error[:200]

        if status == "sent":
            logger.info("email delivery sent", extra=extra)
            return
        if status == "retrying":
            logger.warning("email delivery retry scheduled", extra=extra)
            return
        if status == "cancelled":
            logger.info("email delivery cancelled", extra=extra)
            return
        logger.error("email delivery failed", extra=extra)

    def _delivery_audit_event_id(self, record: EmailOutboxRecord) -> str | None:
        if record.kind != "test":
            return None
        return record.delivery_audit_event_id or str(
            uuid5(NAMESPACE_URL, f"deltallm:email-delivery:{record.email_id}")
        )

    async def _process_delivery_audit(self, record: EmailOutboxRecord) -> None:
        claim_token = record.delivery_audit_claim_token
        if not claim_token:
            logger.error(
                "email delivery audit claim has no fencing token",
                extra={"email_id": record.email_id},
            )
            return
        stop_renewal = asyncio.Event()
        renewal_task = asyncio.create_task(
            self._maintain_delivery_audit_lease(record, claim_token, stop_renewal)
        )
        try:
            await self._process_delivery_audit_claim(record, claim_token=claim_token)
        finally:
            stop_renewal.set()
            with suppress(asyncio.CancelledError):
                await renewal_task

    async def _maintain_delivery_audit_lease(
        self,
        record: EmailOutboxRecord,
        claim_token: str,
        stop: asyncio.Event,
    ) -> None:
        interval = max(1.0, self.config.audit_lease_seconds / 3)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                pass
            try:
                renewed = await self.repository.renew_delivery_audit_claim(
                    email_id=record.email_id,
                    worker_id=self._worker_id,
                    claim_token=claim_token,
                    lease_seconds=self.config.audit_lease_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "email delivery audit lease renewal failed",
                    extra={"email_id": record.email_id},
                )
                return
            if not renewed:
                return

    async def _process_delivery_audit_claim(
        self, record: EmailOutboxRecord, *, claim_token: str
    ) -> None:
        try:
            if self.audit_service is None:
                raise RuntimeError("required email delivery audit service is unavailable")
            await enqueue_audit_event(
                self.audit_service,
                AuditEventInput(
                    action=AuditAction.EMAIL_DELIVERY_RESULT.value,
                    actor_type="platform_account" if record.created_by_account_id else "system",
                    actor_id=record.created_by_account_id,
                    resource_type="email",
                    resource_id=record.email_id,
                    status=record.status,
                    error_type="EmailDeliveryError" if record.last_error else None,
                    metadata={
                        "email_kind": record.kind,
                        "provider": record.provider,
                        "template_key": record.template_key,
                        "attempt_count": record.attempt_count,
                        "max_attempts": record.max_attempts,
                        "provider_message_id": record.last_provider_message_id,
                        "error": record.last_error[:200] if record.last_error else None,
                    },
                    event_id=record.delivery_audit_event_id,
                ),
                delivery_class=AuditDeliveryClass.REQUIRED,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            retry_delay_seconds = self._retry_delay_seconds(record.delivery_audit_attempt_count)
            try:
                await self.repository.mark_delivery_audit_retry(
                    email_id=record.email_id,
                    worker_id=self._worker_id,
                    claim_token=claim_token,
                    error=str(exc),
                    next_attempt_at=datetime.now(tz=UTC) + timedelta(seconds=retry_delay_seconds),
                )
            except Exception:
                logger.exception(
                    "email delivery audit retry transition failed; lease recovery will retry",
                    extra={"email_id": record.email_id},
                )
            logger.warning(
                "email delivery audit reconciliation failed",
                extra={
                    "email_id": record.email_id,
                    "audit_attempt_count": record.delivery_audit_attempt_count,
                },
            )
            return

        try:
            persisted = await self.repository.mark_delivery_audited(
                email_id=record.email_id,
                worker_id=self._worker_id,
                claim_token=claim_token,
            )
        except Exception:
            logger.exception(
                "email delivery audit acknowledgement failed; stable event identity makes recovery idempotent",
                extra={"email_id": record.email_id},
            )
            return
        if not persisted:
            logger.warning(
                "email delivery audit ownership was lost before acknowledgement",
                extra={"email_id": record.email_id},
            )
