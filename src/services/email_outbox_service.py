from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import logging
from time import perf_counter
from typing import Any, Iterable

from src.db.email import EmailOutboxRecord, EmailOutboxRepository
from src.audit.actions import AuditAction
from src.email.models import EmailConfigurationError, EmailDeliveryError, PreparedEmail
from src.metrics import increment_email_delivery_attempt, observe_email_delivery_latency, set_email_queue_depth
from src.services.audit_service import AuditEventInput, AuditService
from src.services.email_delivery_service import EmailDeliveryService

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
        return tuple(address for address in addresses if _normalize_address(address) not in suppressed)

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
    normalized = {_normalize_address(address) for address in suppressed if _normalize_address(address)}
    if not normalized:
        return prepared, set()
    return _apply_suppressed_recipients(prepared, suppressed=normalized), normalized


@dataclass
class EmailWorkerConfig:
    poll_interval_seconds: float = 5.0
    max_batch_size: int = 10
    max_concurrency: int = 3


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

    def with_repository(self, repository: EmailOutboxRepository, *, feedback_repository=None) -> EmailOutboxService:  # noqa: ANN001
        return EmailOutboxService(
            repository=repository,
            delivery_service=self.delivery_service,
            config_getter=self._config_getter,
            feedback_repository=self.feedback_repository if feedback_repository is None else feedback_repository,
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
        record = EmailOutboxRecord(
            email_id="",
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
            status="cancelled" if not prepared.to_addresses else "queued",
            max_attempts=self._max_attempts(),
            next_attempt_at=datetime.now(tz=UTC),
            created_by_account_id=created_by_account_id,
            last_error=None if prepared.to_addresses else "all recipients are suppressed",
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

    def stop(self) -> None:
        self._stopped = True

    async def run(self) -> None:
        while not self._stopped:
            processed = await self.process_once()
            if processed == 0:
                await asyncio.sleep(self.config.poll_interval_seconds)

    async def process_once(self) -> int:
        claimed = await self.repository.claim_due(limit=self.config.max_batch_size)
        if not claimed:
            set_email_queue_depth(await self.repository.count_pending())
            return 0

        semaphore = asyncio.Semaphore(max(1, min(self.config.max_concurrency, len(claimed))))

        async def _run(record: EmailOutboxRecord) -> None:
            async with semaphore:
                await self._process_record(record)

        await asyncio.gather(*[_run(record) for record in claimed])

        set_email_queue_depth(await self.repository.count_pending())
        return len(claimed)

    async def _process_record(self, record: EmailOutboxRecord) -> None:
        started = perf_counter()
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
                await self.repository.cancel(record.email_id, reason=reason)
                increment_email_delivery_attempt(provider=record.provider, kind=record.kind, status="cancelled")
                self._log_delivery(record=record, status="cancelled", error=reason)
                await self._emit_delivery_audit(record=record, status="cancelled", error=reason)
                return
            result = await self.delivery_service.send_prepared_email(prepared)
            await self.repository.mark_sent(record.email_id, provider_message_id=result.provider_message_id)
            increment_email_delivery_attempt(provider=record.provider, kind=record.kind, status="sent")
            self._log_delivery(record=record, status="sent", provider_message_id=result.provider_message_id)
            await self._emit_delivery_audit(record=record, status="sent", provider_message_id=result.provider_message_id)
        except Exception as exc:  # pragma: no cover - defensive path
            error = str(exc)
            should_retry = self._should_retry(record=record, exc=exc)
            if not should_retry:
                await self.repository.mark_failed(record.email_id, error=error)
                increment_email_delivery_attempt(provider=record.provider, kind=record.kind, status="failed")
                self._log_delivery(record=record, status="failed", error=error)
                await self._emit_delivery_audit(record=record, status="failed", error=error)
            else:
                retry_delay_seconds = self._retry_delay_seconds(record.attempt_count)
                await self.repository.mark_retry(
                    record.email_id,
                    error=error,
                    next_attempt_at=datetime.now(tz=UTC) + timedelta(seconds=retry_delay_seconds),
                )
                increment_email_delivery_attempt(provider=record.provider, kind=record.kind, status="retrying")
                self._log_delivery(record=record, status="retrying", error=error, retry_delay_seconds=retry_delay_seconds)
        finally:
            observe_email_delivery_latency(provider=record.provider, kind=record.kind, latency_seconds=perf_counter() - started)

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

    async def _reapply_suppressions(self, *, record: EmailOutboxRecord, prepared: PreparedEmail) -> PreparedEmail:
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

    async def _emit_delivery_audit(
        self,
        *,
        record: EmailOutboxRecord,
        status: str,
        error: str | None = None,
        provider_message_id: str | None = None,
    ) -> None:
        if self.audit_service is None or record.kind != "test":
            return
        self.audit_service.record_event(
            AuditEventInput(
                action=AuditAction.EMAIL_DELIVERY_RESULT.value,
                actor_type="platform_account" if record.created_by_account_id else "system",
                actor_id=record.created_by_account_id,
                resource_type="email",
                resource_id=record.email_id,
                status=status,
                error_type="EmailDeliveryError" if error else None,
                metadata={
                    "email_kind": record.kind,
                    "provider": record.provider,
                    "template_key": record.template_key,
                    "attempt_count": record.attempt_count,
                    "max_attempts": record.max_attempts,
                    "provider_message_id": provider_message_id,
                    "error": error[:200] if error else None,
                },
            ),
            critical=True,
        )
