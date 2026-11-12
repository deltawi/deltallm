"""Attach request and response content to an audit event that another owner writes.

With accounting v2, the accounting worker writes the audit event of a data-plane
request (``ACCOUNTING_PROVIDER_FINALIZATION``). That event has no request or
response body, because large bodies stay out of the accounting write. The request
path sends the bodies separately, as an ``audit_content`` outbox record that names
the target event. The audit worker then:

- attaches the bodies to the target event when the event exists and belongs to
  the record's organization (the organization whose content policy it checked);
- puts the record back in the queue when the event does not exist yet, because
  the accounting projection writes it a little later. The wait grows with the
  age of the record (2 s, then half the age, at most 30 s), so a target that
  never arrives costs about 18 claims and not one every 2 s;
- after a bounded wait, writes the content as its own audit event, so that the
  content is not lost when the target event does not arrive (for example when an
  expired reservation closes the operation with another event). A target of
  another organization gets the same fallback at once: content never attaches
  across organizations.

This module has no lifecycle. The audit worker calls it inside its fenced batch
transaction, so the attach, the event flag and the queue completion commit together.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from src.db.audit.repository import AuditEventRecord, AuditPayloadRecord, AuditRepository
from src.metrics.audit import increment_audit_content_records

AUDIT_CONTENT_RECORD_TYPE = "audit_content"
# The accounting projection usually writes the target event within a few seconds.
AUDIT_CONTENT_DEFER_SECONDS = 2.0
AUDIT_CONTENT_MAX_DEFER_SECONDS = 30.0
AUDIT_CONTENT_ATTACH_WAIT_SECONDS = 300.0
AUDIT_CONTENT_WAITING_REASON = "waiting_for_audit_event"

_TARGET_KEY = "target_event_id"
_ENQUEUED_AT_KEY = "enqueued_at"


@dataclass(frozen=True, slots=True)
class AuditContentTarget:
    event_id: str
    enqueued_at: datetime


@dataclass(frozen=True, slots=True)
class PendingAuditContent:
    """One claimed content record, prepared for both the attach and the fallback."""

    outbox_event_id: str
    # The record's organization: its content policy is the one the worker checked.
    organization_id: str | None
    target: AuditContentTarget
    attached_payloads: list[AuditPayloadRecord]
    fallback_event: AuditEventRecord
    fallback_payloads: list[AuditPayloadRecord]


@dataclass(frozen=True, slots=True)
class AuditContentResult:
    # Outbox event ID -> seconds until the next claim.
    deferred: dict[str, float]
    attached: int = 0
    fallback: int = 0


def audit_content_defer_seconds(
    age_seconds: float, *, attach_wait_seconds: float = AUDIT_CONTENT_ATTACH_WAIT_SECONDS
) -> float:
    """Return the wait before the next claim of a record whose target is missing.

    Half the record's age, from 2 s to 30 s, and not past the end of the attach
    wait, so the fallback still happens on time.
    """

    delay = min(AUDIT_CONTENT_MAX_DEFER_SECONDS, max(AUDIT_CONTENT_DEFER_SECONDS, age_seconds / 2))
    return max(AUDIT_CONTENT_DEFER_SECONDS, min(delay, attach_wait_seconds - age_seconds))


def audit_content_record_id(target_event_id: str) -> str:
    """Return the stable outbox ID of the content record for one target event."""

    return str(uuid5(UUID(str(target_event_id)), "audit-content:v1"))


def audit_content_fields(*, target_event_id: str, enqueued_at: datetime) -> dict[str, str]:
    """Return the fields that make a serialized audit item an ``audit_content`` record."""

    if enqueued_at.tzinfo is None:
        raise ValueError("audit content enqueue time must have a time zone")
    return {
        _TARGET_KEY: str(UUID(str(target_event_id))),
        _ENQUEUED_AT_KEY: enqueued_at.astimezone(UTC).isoformat(),
    }


def audit_content_target(payload: Mapping[str, Any]) -> AuditContentTarget:
    """Read and validate the target of a stored ``audit_content`` record."""

    raw_target = payload.get(_TARGET_KEY)
    raw_enqueued_at = payload.get(_ENQUEUED_AT_KEY)
    if not isinstance(raw_target, str) or not isinstance(raw_enqueued_at, str):
        raise ValueError("audit content record is missing its target or enqueue time")
    enqueued_at = datetime.fromisoformat(raw_enqueued_at)
    if enqueued_at.tzinfo is None:
        raise ValueError("audit content enqueue time must have a time zone")
    return AuditContentTarget(event_id=str(UUID(raw_target)), enqueued_at=enqueued_at)


async def apply_audit_content(
    repository: AuditRepository,
    pending: Sequence[PendingAuditContent],
    *,
    now: datetime,
    attach_wait_seconds: float = AUDIT_CONTENT_ATTACH_WAIT_SECONDS,
) -> AuditContentResult:
    """Attach, defer or fall back for each record. Uses one read and at most three writes."""

    if not pending:
        return AuditContentResult(deferred={})
    owners = await repository.event_organizations([item.target.event_id for item in pending])
    payloads: list[AuditPayloadRecord] = []
    stored_targets: list[str] = []
    fallback_events: list[AuditEventRecord] = []
    deferred: dict[str, float] = {}
    attached = mismatched = 0
    for item in pending:
        age_seconds = (now - item.target.enqueued_at).total_seconds()
        if item.target.event_id in owners:
            if owners[item.target.event_id] == item.organization_id:
                attached += 1
                payloads.extend(item.attached_payloads)
                if any(_has_stored_content(payload) for payload in item.attached_payloads):
                    stored_targets.append(item.target.event_id)
                continue
            # Never attach across organizations: the target's organization may
            # have content storage off, and its audit readers would see the bodies.
            mismatched += 1
        elif age_seconds < attach_wait_seconds:
            deferred[item.outbox_event_id] = audit_content_defer_seconds(
                age_seconds, attach_wait_seconds=attach_wait_seconds
            )
            continue
        fallback_events.append(item.fallback_event)
        payloads.extend(item.fallback_payloads)

    # A fallback event goes in before its payload rows reference it.
    await repository.create_events_batch(fallback_events)
    await repository.create_payloads_batch(payloads)
    await repository.mark_content_stored(stored_targets)
    increment_audit_content_records(outcome="attached", count=attached)
    increment_audit_content_records(outcome="deferred", count=len(deferred))
    increment_audit_content_records(outcome="fallback_event", count=len(fallback_events))
    increment_audit_content_records(outcome="organization_mismatch", count=mismatched)
    return AuditContentResult(
        deferred=deferred,
        attached=attached,
        fallback=len(fallback_events),
    )


def _has_stored_content(payload: AuditPayloadRecord) -> bool:
    return payload.content_json is not None or payload.storage_uri is not None
