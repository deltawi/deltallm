"""Send the request and response bodies of accounting v2 requests to the audit outbox.

With accounting v2, the accounting write records the audit event of a data-plane
request without bodies, and the request path writes no audit event of its own. For
organizations with content storage on, this buffer keeps the bodies in memory for
a short time and a background task sends them as best-effort ``audit_content``
records. The audit worker attaches them to the accounting event.

Request path cost: ``offer`` is synchronous. It does one cached policy lookup and
one list append. It never waits for the database, and it never fails a request.

Database cost: the flusher runs once per second (or earlier when many items wait),
reads uncached policies in one query, and writes one statement per organization
bundle. It runs one statement at a time, so it holds at most one connection of the
audit pool. A database error or a full audit queue drops the rest of that flush;
there is no retry loop.

Memory: at most ``AUDIT_CONTENT_MAX_ITEMS`` items wait between flushes. Each item
holds references to the request and response objects that the request already
built, for about one more second. The bodies that wait or are in flight hold at
most ``AUDIT_CONTENT_MAX_HELD_CHARS`` characters, measured before compaction (an
image request can carry megabytes of base64). A flush sends at most
``AUDIT_CONTENT_MAX_FLUSH_BYTES`` and drops the rest.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
import json
import logging
from typing import TYPE_CHECKING, Any

from src.audit.delivery import AuditDeliveryClass
from src.database_caller import with_database_caller
from src.db.audit.audit_ingestion import AuditOutboxEnvelope
from src.metrics.audit import (
    increment_audit_content_dropped,
    increment_audit_enqueue,
    set_audit_capacity_utilization,
    set_audit_content_buffer_items,
    set_audit_queue_depth,
)
from src.services.audit.content_attachment import (
    AUDIT_CONTENT_RECORD_TYPE,
    audit_content_fields,
    audit_content_record_id,
)

if TYPE_CHECKING:
    from src.services.audit.audit_service import (
        AuditEventInput,
        AuditPayloadInput,
        AuditService,
    )

logger = logging.getLogger(__name__)

AUDIT_CONTENT_FLUSH_INTERVAL_SECONDS = 1.0
AUDIT_CONTENT_MAX_ITEMS = 2_000
AUDIT_CONTENT_EARLY_FLUSH_ITEMS = 500
AUDIT_CONTENT_MAX_HELD_CHARS = 128 * 1024 * 1024
AUDIT_CONTENT_MAX_FLUSH_BYTES = 64 * 1024 * 1024
AUDIT_CONTENT_BUNDLE_MAX_BYTES = 1024 * 1024
AUDIT_CONTENT_BUNDLE_MAX_ITEMS = 100
AUDIT_CONTENT_SHUTDOWN_FLUSH_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class AuditContentItem:
    target_event_id: str
    event: AuditEventInput
    payloads: list[AuditPayloadInput]
    enqueued_at: datetime

    @property
    def organization_id(self) -> str | None:
        return self.event.organization_id


@dataclass(frozen=True, slots=True)
class _SizedEnvelope:
    envelope: AuditOutboxEnvelope
    size: int


class AuditContentBuffer:
    """Owned by the audit bootstrap: one flusher task per process."""

    def __init__(
        self,
        audit_service: AuditService,
        *,
        flush_interval_seconds: float = AUDIT_CONTENT_FLUSH_INTERVAL_SECONDS,
        max_items: int = AUDIT_CONTENT_MAX_ITEMS,
        max_held_chars: int = AUDIT_CONTENT_MAX_HELD_CHARS,
        early_flush_items: int = AUDIT_CONTENT_EARLY_FLUSH_ITEMS,
        max_flush_bytes: int = AUDIT_CONTENT_MAX_FLUSH_BYTES,
        bundle_max_bytes: int = AUDIT_CONTENT_BUNDLE_MAX_BYTES,
        bundle_max_items: int = AUDIT_CONTENT_BUNDLE_MAX_ITEMS,
    ) -> None:
        self.audit_service = audit_service
        self.flush_interval_seconds = max(0.01, float(flush_interval_seconds))
        self.max_items = max(1, int(max_items))
        self.max_held_chars = max(1, int(max_held_chars))
        self.early_flush_items = max(1, min(int(early_flush_items), self.max_items))
        self.max_flush_bytes = max(1, int(max_flush_bytes))
        self.bundle_max_bytes = max(1, int(bundle_max_bytes))
        self.bundle_max_items = max(1, int(bundle_max_items))
        self._items: list[AuditContentItem] = []
        # Approximate characters of the bodies that wait, and of those in flight.
        self._waiting_chars = 0
        self._in_flight_chars = 0
        self._wake = asyncio.Event()
        self._closed = False
        self._task: asyncio.Task[None] | None = None

    # Request path -------------------------------------------------------------

    def offer(self, item: AuditContentItem) -> bool:
        """Keep the item for the next flush. Never waits and never raises."""

        if self._closed:
            increment_audit_content_dropped(reason="closed")
            return False
        if self.audit_service.peek_content_storage_policy(item.organization_id) is False:
            # Content of an organization without content storage never leaves the process.
            return False
        if len(self._items) >= self.max_items:
            increment_audit_content_dropped(reason="buffer_full")
            return False
        size = sum(approximate_chars(payload.content_json) for payload in item.payloads)
        if self._waiting_chars + self._in_flight_chars + size > self.max_held_chars:
            increment_audit_content_dropped(reason="buffer_bytes")
            return False
        self._items.append(item)
        self._waiting_chars += size
        if len(self._items) >= self.early_flush_items:
            self._wake.set()
        return True

    # Lifecycle ----------------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="audit-content-buffer")

    async def stop(
        self, *, flush_timeout_seconds: float = AUDIT_CONTENT_SHUTDOWN_FLUSH_SECONDS
    ) -> None:
        """Stop taking items, try one bounded final flush, then drop what is left.

        A flush that is already running can finish inside the same time limit.
        """

        self._closed = True
        self._wake.set()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, flush_timeout_seconds)
        task, self._task = self._task, None
        try:
            if task is not None:
                # The woken loop sends what waits and exits; at the deadline it is cancelled.
                await asyncio.wait_for(task, timeout=max(0.0, deadline - loop.time()))
            await asyncio.wait_for(self.flush(), timeout=max(0.0, deadline - loop.time()))
        except (TimeoutError, asyncio.TimeoutError):
            pass
        if self._items:
            increment_audit_content_dropped(reason="shutdown", count=len(self._items))
            self._items = []
            self._waiting_chars = 0
        set_audit_content_buffer_items(0)

    @property
    def pending_items(self) -> int:
        return len(self._items)

    async def _run(self) -> None:
        while not self._closed:
            with suppress(TimeoutError, asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.flush_interval_seconds)
            self._wake.clear()
            try:
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception:
                # flush() drops and counts its own failures; this guards the loop.
                logger.exception("audit content flush failed")

    # Flusher ------------------------------------------------------------------

    async def flush(self) -> None:
        items, self._items = self._items, []
        held, self._waiting_chars = self._waiting_chars, 0
        # The items that waited for this flush; at most max_items.
        set_audit_content_buffer_items(len(items))
        if not items:
            return
        # The bodies stay in memory until the send ends, so they still count.
        self._in_flight_chars += held
        try:
            await self._flush(items)
        finally:
            self._in_flight_chars -= held

    async def _flush(self, items: list[AuditContentItem]) -> None:
        organization_ids = {item.organization_id for item in items if item.organization_id}
        try:
            policies = await self.audit_service.content_storage_policies(organization_ids)
        except asyncio.CancelledError:
            self._drop(items, reason="shutdown")
            raise
        except Exception:
            logger.warning("audit content policy read failed; dropping content of this flush")
            self._drop(items, reason="policy_unavailable")
            return
        kept = [item for item in items if policies.get(item.organization_id or "", False)]
        if not kept:
            return
        # Compaction hashes and scans large strings, so it runs off the event loop.
        try:
            sized, dropped = await asyncio.to_thread(self._build_envelopes, kept)
        except asyncio.CancelledError:
            self._drop(kept, reason="shutdown")
            raise
        for reason, count in dropped.items():
            if count:
                increment_audit_content_dropped(reason=reason, count=count)
        await self._send(sized)

    def _build_envelopes(
        self, items: list[AuditContentItem]
    ) -> tuple[list[_SizedEnvelope], dict[str, int]]:
        sized: list[_SizedEnvelope] = []
        total = 0
        dropped = {"duplicate": 0, "flush_bytes": 0}
        seen: set[str] = set()
        for item in items:
            # One accounting event takes one content record. A bundle with the same
            # record twice is refused, so a second item for the same event is dropped.
            if item.target_event_id in seen:
                dropped["duplicate"] += 1
                continue
            seen.add(item.target_event_id)
            envelope = self._envelope(item)
            size = len(json.dumps(envelope.payload, default=str))
            if total + size > self.max_flush_bytes:
                dropped["flush_bytes"] += 1
                continue
            total += size
            sized.append(_SizedEnvelope(envelope=envelope, size=size))
        return sized, dropped

    def _envelope(self, item: AuditContentItem) -> AuditOutboxEnvelope:
        # Compacts like every other audit write; the redacted copy has no content.
        payload, redacted = self.audit_service.serialize_outbox_payloads(item.event, item.payloads)
        fields = audit_content_fields(
            target_event_id=item.target_event_id, enqueued_at=item.enqueued_at
        )
        return AuditOutboxEnvelope(
            event_id=audit_content_record_id(item.target_event_id),
            record_type=AUDIT_CONTENT_RECORD_TYPE,
            organization_id=item.organization_id,
            delivery_class=AuditDeliveryClass.BEST_EFFORT,
            payload=payload | fields,
            redacted_payload=redacted | fields,
            max_attempts=self.audit_service.ingestion_config.max_attempts,
        )

    @with_database_caller("audit")
    async def _enqueue_bundle(self, bundle: list[AuditOutboxEnvelope]) -> dict[str, str]:
        # Best-effort capacity only: required_reserve stays free for required events.
        # The worker is not woken: the accounting event is usually not written yet,
        # and the worker's own poll (at most 1 s when idle) is soon enough.
        config = self.audit_service.ingestion_config
        result = await self.audit_service.worker_ingestion_repository.enqueue_bundle(
            envelopes=bundle,
            max_pending_events=config.max_pending_events,
            required_reserve=config.required_reserve,
        )
        for status in result.statuses.values():
            increment_audit_enqueue(
                record_type=AUDIT_CONTENT_RECORD_TYPE,
                delivery_class=AuditDeliveryClass.BEST_EFFORT.value,
                outcome=status,
            )
        set_audit_queue_depth(result.pending_count)
        set_audit_capacity_utilization(
            pending=result.pending_count, capacity=config.max_pending_events
        )
        return dict(result.statuses)

    async def _send(self, sized: list[_SizedEnvelope]) -> None:
        by_organization: dict[str | None, list[_SizedEnvelope]] = defaultdict(list)
        for entry in sized:
            by_organization[entry.envelope.organization_id].append(entry)
        bundles = [
            bundle
            for entries in by_organization.values()
            for bundle in _bundles(entries, self.bundle_max_bytes, self.bundle_max_items)
        ]
        for index, bundle in enumerate(bundles):
            try:
                statuses = await self._enqueue_bundle(bundle)
            except asyncio.CancelledError:
                remaining = sum(len(rest) for rest in bundles[index:])
                increment_audit_content_dropped(reason="shutdown", count=remaining)
                raise
            except Exception:
                # The database is busy or down: shed this flush instead of retrying.
                remaining = sum(len(rest) for rest in bundles[index:])
                logger.warning(
                    "audit content enqueue failed; dropping content of this flush",
                    extra={"dropped": remaining},
                )
                increment_audit_content_dropped(reason="enqueue_failed", count=remaining)
                return
            for status, reason in (("full", "queue_full"), ("duplicate", "duplicate")):
                count = sum(1 for value in statuses.values() if value == status)
                if count:
                    increment_audit_content_dropped(reason=reason, count=count)
            if "full" in statuses.values():
                # The queue has no best-effort room: do not send more statements now.
                rest = sum(len(later) for later in bundles[index + 1 :])
                increment_audit_content_dropped(reason="queue_full", count=rest)
                return

    @staticmethod
    def _drop(items: list[AuditContentItem], *, reason: str) -> None:
        increment_audit_content_dropped(reason=reason, count=len(items))


def approximate_chars(value: Any) -> int:
    """Return a rough JSON size of a body without copying or scanning its strings.

    It only adds up string lengths and a few characters for each other value,
    so its cost depends on the number of values, not on their size.
    """

    total = 0
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            total += len(current) + 2
        elif isinstance(current, dict):
            total += 2
            for key, item in current.items():
                total += (len(key) if isinstance(key, str) else 8) + 4
                stack.append(item)
        elif isinstance(current, (list, tuple)):
            total += 2 + len(current)
            stack.extend(current)
        else:
            total += 8
    return total


def _bundles(
    entries: Iterable[_SizedEnvelope], max_bytes: int, max_items: int
) -> list[list[AuditOutboxEnvelope]]:
    bundles: list[list[AuditOutboxEnvelope]] = []
    current: list[AuditOutboxEnvelope] = []
    current_bytes = 0
    for entry in entries:
        if current and (current_bytes + entry.size > max_bytes or len(current) >= max_items):
            bundles.append(current)
            current, current_bytes = [], 0
        current.append(entry.envelope)
        current_bytes += entry.size
    if current:
        bundles.append(current)
    return bundles
