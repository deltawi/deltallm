"""Send the request and response bodies of accounting v2 requests to the audit outbox.

With accounting v2, the accounting write records the audit event of a data-plane
request without bodies, and the request path writes no audit event of its own. For
organizations with content storage on, this buffer keeps the bodies in memory for
a short time and a background task sends them as best-effort ``audit_content``
records. The audit worker attaches them to the accounting event.

Request path cost: ``offer`` is synchronous. It does one cached policy lookup, a
size estimate, and one list append. It never waits for the database, and it never
fails a request. A request body that the text or embeddings preflight already
serialized for its token estimate (after prompt resolution, hooks and guardrails)
is kept as that JSON text, the request as sent upstream: it is counted by its
length and not visited again. Other bodies are measured and copied with a work
limit of ``AUDIT_CONTENT_MAX_SIZE_VALUES`` values. A body over that limit is
replaced by a placeholder: the one of compaction when its size surely exceeds the
payload limit (for example a large embeddings response), or
``payload_too_many_values``. So every body that is kept is counted by a measured
size.

Database cost: the flusher runs once per second (or earlier when many items wait),
reads uncached policies in one query, and writes one statement per organization
bundle. It runs one statement at a time, so it holds at most one connection of the
audit pool. Every statement, measured as it is sent, is at most
``AUDIT_CONTENT_BUNDLE_MAX_BYTES``: a record that is larger is compacted again with
a smaller payload limit, or dropped. A database error or a full audit queue drops
the rest of that flush; there is no retry loop.

Memory: at most ``AUDIT_CONTENT_MAX_ITEMS`` items wait between flushes. Each item
holds a snapshot that later changes to the request's objects (for example by a
post-call hook) cannot reach: the preflight's JSON text of the request, and copies
of the containers of the other bodies (strings and numbers are shared, so a copy
costs about one reference per value). The bodies that wait or are in flight hold
at most ``AUDIT_CONTENT_MAX_HELD_CHARS`` characters, measured before compaction (an
image request can carry megabytes of base64). A flush sends at most
``AUDIT_CONTENT_MAX_FLUSH_BYTES`` and drops the rest.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime
import json
import logging
from typing import TYPE_CHECKING, Any

from src.audit.delivery import AuditDeliveryClass
from src.database_caller import with_database_caller
from src.db.audit.audit_ingestion import AuditOutboxEnvelope, audit_envelope_wire
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
from src.services.audit.payload_compaction import (
    ENTRY_OVERHEAD,
    MIN_VALUE_SIZE,
    oversize_placeholder,
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
# Work limit of the measure and copy on the request path, for each body: about 2 to
# 3 ms for large real bodies, at most about 5 ms for a body made only of empty
# containers. Request bodies that the preflight serialized are not walked.
AUDIT_CONTENT_MAX_SIZE_VALUES = 16_384
# Attempts to compact a record into one statement before it is dropped.
_FIT_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class AuditContentItem:
    target_event_id: str
    event: AuditEventInput
    payloads: list[AuditPayloadInput]
    enqueued_at: datetime
    # JSON text of the request body as sent upstream, taken by the preflight. When it
    # is set, the request payload has no content_json: the text is the only copy,
    # so later changes to the request object cannot change what the buffer holds.
    request_json: str | None = None

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
        max_size_values: int = AUDIT_CONTENT_MAX_SIZE_VALUES,
    ) -> None:
        self.audit_service = audit_service
        self.flush_interval_seconds = max(0.01, float(flush_interval_seconds))
        self.max_items = max(1, int(max_items))
        self.max_held_chars = max(1, int(max_held_chars))
        self.early_flush_items = max(1, min(int(early_flush_items), self.max_items))
        self.max_flush_bytes = max(1, int(max_flush_bytes))
        self.bundle_max_bytes = max(1, int(bundle_max_bytes))
        self.bundle_max_items = max(1, int(bundle_max_items))
        self.max_size_values = max(1, int(max_size_values))
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
        item, size = self._bounded_size(item)
        if self._waiting_chars + self._in_flight_chars + size > self.max_held_chars:
            increment_audit_content_dropped(reason="buffer_bytes")
            return False
        self._items.append(item)
        self._waiting_chars += size
        if len(self._items) >= self.early_flush_items:
            self._wake.set()
        return True

    def _bounded_size(self, item: AuditContentItem) -> tuple[AuditContentItem, int]:
        """Size each body with the work limit.

        Returns the item, with a placeholder for each body over the limit, and the
        characters it counts. Every body that is kept has a measured size.
        """

        total = len(item.request_json) if item.request_json is not None else 0
        payloads = list(item.payloads)
        for index, payload in enumerate(payloads):
            if payload.content_json is None:
                continue
            # Each body has its own limit, so a large request does not cost the
            # response its content. A body that fits is copied while it is measured,
            # so later changes to the caller's object do not reach the buffer.
            size = _measure(payload.content_json, self.max_size_values)
            if size.complete:
                payloads[index] = replace(payload, content_json=size.copy)
                total += size.chars
                continue
            payload_limit = self.audit_service.ingestion_config.payload_max_bytes
            if size.floor > payload_limit:
                # Compaction would replace it later, so the placeholder is made now.
                placeholder = oversize_placeholder(size.floor, max_bytes=payload_limit)
            else:
                placeholder = {
                    "omitted": "payload_too_many_values",
                    "max_values": self.max_size_values,
                }
                increment_audit_content_dropped(reason="payload_values")
            payloads[index] = replace(payload, content_json=placeholder)
            total += _PLACEHOLDER_CHARS
        return replace(item, payloads=payloads), total

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
        dropped = {"duplicate": 0, "flush_bytes": 0, "record_bytes": 0}
        seen: set[str] = set()
        for item in items:
            # One accounting event takes one content record. A bundle with the same
            # record twice is refused, so a second item for the same event is dropped.
            if item.target_event_id in seen:
                dropped["duplicate"] += 1
                continue
            seen.add(item.target_event_id)
            fitted = self._fitted_envelope(item)
            if fitted is None:
                dropped["record_bytes"] += 1
                continue
            if total + fitted.size > self.max_flush_bytes:
                dropped["flush_bytes"] += 1
                continue
            total += fitted.size
            sized.append(fitted)
        return sized, dropped

    def _fitted_envelope(self, item: AuditContentItem) -> _SizedEnvelope | None:
        """Return the record if it fits one statement, compacting it harder if needed.

        Compaction limits each body on its own, so the bodies of one record can
        together be larger than a statement. JSON escaping can add more.
        """

        item = _with_request_body(item)
        record_max = self.bundle_max_bytes - _BUNDLE_BRACKETS
        limit: int | None = None
        for _ in range(_FIT_ATTEMPTS):
            envelope = self._envelope(item, max_payload_bytes=limit)
            size = _wire_size(envelope)
            if size <= record_max:
                return _SizedEnvelope(envelope=envelope, size=size)
            current = limit or self.audit_service.ingestion_config.payload_max_bytes
            limit = max(1, current * record_max // (2 * size))
        return None

    def _envelope(
        self, item: AuditContentItem, *, max_payload_bytes: int | None = None
    ) -> AuditOutboxEnvelope:
        # Compacts like every other audit write; the redacted copy has no content.
        payload, redacted = self.audit_service.serialize_outbox_payloads(
            item.event, item.payloads, max_payload_bytes=max_payload_bytes
        )
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


def approximate_chars(value: Any, *, max_values: int = AUDIT_CONTENT_MAX_SIZE_VALUES) -> int | None:
    """Return a rough JSON size of a body, or ``None`` if it has more than ``max_values`` values.

    It only adds up string lengths and a few characters for each other value, so
    its cost depends on the number of values, not on their size.
    """

    size = _measure(value, max_values)
    return size.chars if size.complete else None


@dataclass(frozen=True, slots=True)
class _Size:
    # Rough JSON size of the values visited.
    chars: int
    # Lower bound of the size that compaction computes for the whole body.
    floor: int
    visited: int
    # False when some container was not expanded (over the work limit).
    complete: bool
    # A copy of the body (containers copied, strings and numbers shared) when complete.
    copy: Any = None


# Characters counted for a placeholder that replaces a body.
_PLACEHOLDER_CHARS = 96


def _measure(value: Any, budget: int) -> _Size:
    """Measure and copy a body with a work limit.

    A container is expanded only when all its values fit in what is left of the
    budget, so a long list is never iterated. A copied container counts as two
    values, so bodies made of many small containers cost about as much as bodies
    made of many numbers or strings. A container that does not fit adds
    only its length to the floor (each of its values has a compacted size of at
    least ``MIN_VALUE_SIZE``), and no copy is returned. The work is at most about
    twice the budget: the values visited, plus the values that wait when the budget
    is reached.

    The floor follows ``compact_audit_content``: a string counts only its quotes,
    because compaction can shorten inline data.
    """

    chars = 0
    floor = 0
    visited = 0
    complete = True
    root: list[Any] = [None]
    # Containers that wait: (value, container of its copy, key or index in it).
    # Strings and scalars are counted where they are found, and their copy shares them.
    stack: list[tuple[Any, Any, Any]] = [(value, root, 0)]
    push = stack.append
    pop = stack.pop
    containers = (dict, list, tuple)
    while stack:
        current, parent, key = pop()
        visited += 1
        if isinstance(current, dict):
            if visited + len(stack) + len(current) > budget:
                complete = False
                floor += 2 + len(current) * (ENTRY_OVERHEAD + MIN_VALUE_SIZE)
                continue
            copy = dict(current)
            parent[key] = copy
            visited += 1  # the copy costs about one more value
            chars += 2
            floor += 2
            for item_key, item in current.items():
                if isinstance(item_key, str):
                    chars += len(item_key) + 4
                    floor += len(item_key) + ENTRY_OVERHEAD
                else:
                    chars += 12
                    floor += ENTRY_OVERHEAD
                if isinstance(item, str):
                    visited += 1
                    chars += len(item) + 2
                    floor += MIN_VALUE_SIZE
                elif isinstance(item, containers):
                    push((item, copy, item_key))
                else:
                    visited += 1
                    chars += 8
                    floor += ENTRY_OVERHEAD
        elif isinstance(current, (list, tuple)):
            if visited + len(stack) + len(current) > budget:
                complete = False
                floor += 2 + len(current) * (1 + MIN_VALUE_SIZE)
                continue
            items = list(current)
            parent[key] = items
            visited += 1  # the copy costs about one more value
            chars += 2 + len(current)
            floor += 2 + len(current)
            for index, item in enumerate(current):
                if isinstance(item, str):
                    visited += 1
                    chars += len(item) + 2
                    floor += MIN_VALUE_SIZE
                elif isinstance(item, containers):
                    push((item, items, index))
                else:
                    visited += 1
                    chars += 8
                    floor += ENTRY_OVERHEAD
        elif isinstance(current, str):
            chars += len(current) + 2
            floor += MIN_VALUE_SIZE
            parent[key] = current
        else:
            chars += 8
            floor += ENTRY_OVERHEAD
            parent[key] = current
    return _Size(
        chars=chars,
        floor=floor,
        visited=visited,
        complete=complete,
        copy=root[0] if complete else None,
    )


def _with_request_body(item: AuditContentItem) -> AuditContentItem:
    """Parse the request text that the preflight kept (in the flusher's thread)."""

    if item.request_json is None:
        return item
    body = json.loads(item.request_json)
    payloads = [
        replace(payload, content_json=body)
        if payload.kind == "request" and payload.content_json is None
        else payload
        for payload in item.payloads
    ]
    return replace(item, payloads=payloads, request_json=None)


def audit_content_may_be_stored(request: Any, organization_id: str | None) -> bool:
    """Return False when the content of this request is surely not stored. No I/O."""

    state = getattr(getattr(request, "app", None), "state", None)
    buffer = getattr(state, "audit_content_buffer", None)
    if buffer is None:
        return False
    return buffer.audit_service.peek_content_storage_policy(organization_id) is not False


# The statement sends a JSON list: "[" and "]", and ", " between records.
_BUNDLE_BRACKETS = 2
_BUNDLE_SEPARATOR = 2


def _wire_size(envelope: AuditOutboxEnvelope) -> int:
    """Return the exact size of one record in the statement that sends it."""

    return len(json.dumps(audit_envelope_wire(envelope), default=str))


def _bundles(
    entries: Iterable[_SizedEnvelope], max_bytes: int, max_items: int
) -> list[list[AuditOutboxEnvelope]]:
    """Group records so that each statement is at most ``max_bytes`` as sent.

    Every record already fits alone (see ``_fitted_envelope``).
    """

    bundles: list[list[AuditOutboxEnvelope]] = []
    current: list[AuditOutboxEnvelope] = []
    current_bytes = _BUNDLE_BRACKETS
    for entry in entries:
        if current and (
            current_bytes + _BUNDLE_SEPARATOR + entry.size > max_bytes or len(current) >= max_items
        ):
            bundles.append(current)
            current, current_bytes = [], _BUNDLE_BRACKETS
        if current:
            current_bytes += _BUNDLE_SEPARATOR
        current.append(entry.envelope)
        current_bytes += entry.size
    if current:
        bundles.append(current)
    return bundles
