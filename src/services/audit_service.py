from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from src.db.repositories import AuditEventRecord, AuditPayloadRecord, AuditRepository

logger = logging.getLogger(__name__)


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


@dataclass
class _QueueItem:
    event: AuditEventInput
    payloads: list[AuditPayloadInput] = field(default_factory=list)
    critical: bool = False


class AuditService:
    def __init__(self, repository: AuditRepository, *, queue_max_size: int = 1024) -> None:
        self.repository = repository
        self._queue: asyncio.Queue[_QueueItem] = asyncio.Queue(maxsize=max(1, queue_max_size))
        self._worker_task: asyncio.Task[Any] | None = None
        self._fallback_tasks: set[asyncio.Task[Any]] = set()
        self._closed = False
        self.dropped_events = 0
        self.failed_events = 0

    async def start(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._closed = False
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def shutdown(self) -> None:
        self._closed = True
        if self._worker_task is not None:
            await self._queue.join()
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        if self._fallback_tasks:
            await asyncio.gather(*list(self._fallback_tasks), return_exceptions=True)
            self._fallback_tasks.clear()

    def record_event(self, event: AuditEventInput, *, payloads: list[AuditPayloadInput] | None = None, critical: bool = False) -> None:
        if self._closed:
            logger.warning("audit service is closed; dropping event", extra={"action": event.action})
            self.dropped_events += 1
            return

        item = _QueueItem(event=event, payloads=list(payloads or []), critical=critical)
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            if critical:
                self._schedule_fallback(item)
            else:
                self.dropped_events += 1
                logger.warning("audit queue full; dropping non-critical event", extra={"action": event.action})

    async def record_event_sync(
        self,
        event: AuditEventInput,
        *,
        payloads: list[AuditPayloadInput] | None = None,
    ) -> None:
        await self._persist(_QueueItem(event=event, payloads=list(payloads or []), critical=True))

    async def _worker_loop(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                await self._persist(item)
            except Exception:
                self.failed_events += 1
                logger.exception(
                    "failed to persist audit event",
                    extra={"action": item.event.action, "critical": item.critical},
                )
                if item.critical:
                    self._schedule_fallback(item)
            finally:
                self._queue.task_done()

    async def _persist(self, item: _QueueItem) -> None:
        content_enabled = await self.repository.is_content_storage_enabled_for_org(item.event.organization_id)
        payload_records: list[AuditPayloadRecord] = []
        content_stored = False

        for payload in item.payloads:
            has_content = payload.has_content()
            if content_enabled:
                payload_records.append(
                    AuditPayloadRecord(
                        payload_id="",
                        event_id="",
                        kind=payload.kind,
                        storage_mode=payload.storage_mode,
                        content_json=payload.content_json,
                        storage_uri=payload.storage_uri,
                        content_sha256=payload.content_sha256,
                        size_bytes=payload.size_bytes,
                        redacted=payload.redacted,
                    )
                )
                content_stored = content_stored or has_content
            else:
                payload_records.append(
                    AuditPayloadRecord(
                        payload_id="",
                        event_id="",
                        kind=payload.kind,
                        storage_mode=payload.storage_mode,
                        content_json=None,
                        storage_uri=None,
                        content_sha256=payload.content_sha256,
                        size_bytes=payload.size_bytes,
                        redacted=True if has_content else payload.redacted,
                    )
                )

        stored_event = await self.repository.create_event(
            AuditEventRecord(
                event_id="",
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
            )
        )

        for payload_record in payload_records:
            payload_record.event_id = stored_event.event_id
            await self.repository.create_payload(payload_record)

    def _schedule_fallback(self, item: _QueueItem) -> None:
        try:
            task = asyncio.create_task(self._persist(item))
        except RuntimeError:
            self.dropped_events += 1
            logger.warning("unable to schedule fallback audit write", extra={"action": item.event.action})
            return
        self._fallback_tasks.add(task)
        task.add_done_callback(self._fallback_tasks.discard)
