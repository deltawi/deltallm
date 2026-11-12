from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from fastapi import Request

from src.audit.actions import AuditAction, normalize_audit_action
from src.audit.delivery import AuditDeliveryClass
from src.audit.errors import derive_audit_error_code
from src.client_address import request_client_ip
from src.billing.accounting.accounting_finalization import (
    accounting_finalization_audit_event_id,
)
from src.billing.accounting.accounting_protocol import AccountingOperationHandle
from src.services.audit.audit_service import (
    AuditEventInput,
    AuditPayloadInput,
    AuditService,
    enqueue_audit_event,
)
from src.services.audit.content_buffer import (
    AuditContentItem,
    AuditRequestSnapshot,
    audit_content_may_be_stored,
)


def accounting_operation(request: Request) -> AccountingOperationHandle | None:
    """Return the accounting v2 operation of this request, if it has one."""

    handle = getattr(request.state, "spend_operation_handle", None)
    return handle if isinstance(handle, AccountingOperationHandle) else None


def accounting_audit_content_wanted(request: Request, organization_id: str | None) -> bool:
    """Return False when the content of this request is surely not stored. No I/O."""

    return audit_content_may_be_stored(request, organization_id)


def offer_accounting_audit_content(
    request: Request,
    operation: AccountingOperationHandle,
    *,
    event: AuditEventInput,
    payloads: list[AuditPayloadInput],
) -> None:
    """Hand the bodies of an accounting v2 request to the content buffer.

    The accounting write already records the audit event of the request, without
    bodies. This call only appends to a bounded in-memory buffer: it never waits
    and never fails the request. The audit worker attaches the bodies to that
    event later, for organizations with content storage on.
    """

    buffer = getattr(request.app.state, "audit_content_buffer", None)
    if buffer is None or not any(payload.has_content() for payload in payloads):
        return
    operation_id = operation.reservation.operation_id
    event.metadata = {**(event.metadata or {}), "accounting_operation_id": str(operation_id)}
    snapshot = _preflight_request_snapshot(request, payloads)
    if snapshot is not None:
        # The buffer keeps the snapshot (a text, or a copy), not the request object.
        payloads = [
            replace(payload, content_json=snapshot.body) if payload.kind == "request" else payload
            for payload in payloads
        ]
    buffer.offer(
        AuditContentItem(
            target_event_id=accounting_finalization_audit_event_id(operation_id),
            event=event,
            payloads=payloads,
            enqueued_at=datetime.now(UTC),
            request_snapshot=snapshot,
        )
    )


def _preflight_request_snapshot(
    request: Request, payloads: list[AuditPayloadInput]
) -> AuditRequestSnapshot | None:
    """Return the snapshot the preflight took of the request body, if this is that body.

    The text and embeddings preflights take it after prompt resolution, hooks and
    guardrails, when content may be stored: the request as sent upstream. Unlike the
    request object it cannot change later (post-call hooks get the same object).
    """

    body = next((payload.content_json for payload in payloads if payload.kind == "request"), None)
    if body is None:
        return None
    for name in ("prepared_text_request", "prepared_embedding_request"):
        prepared = getattr(request.state, name, None)
        if prepared is not None and getattr(prepared, "request_data", None) is body:
            snapshot = getattr(prepared, "audit_request", None)
            return snapshot if isinstance(snapshot, AuditRequestSnapshot) else None
    return None


async def emit_audit_event(
    *,
    request: Request,
    request_start: float,
    action: str | AuditAction,
    status: str,
    actor_type: str,
    actor_id: str | None = None,
    organization_id: str | None = None,
    api_key: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    request_payload: dict[str, Any] | None = None,
    response_payload: dict[str, Any] | None = None,
    error: Exception | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    metadata: dict[str, Any] | None = None,
    critical: bool = True,
    event_id: str | None = None,
) -> None:
    # The accounting finalization ACK already contains the required redacted
    # audit envelope. Its projection worker writes the compatibility audit row;
    # a second request-path enqueue would reintroduce per-request lifecycle I/O.
    # Only the bodies go to the best-effort content buffer, without waiting.
    operation = accounting_operation(request)
    if operation is not None and not accounting_audit_content_wanted(request, organization_id):
        return
    audit_service: AuditService | None = getattr(request.app.state, "audit_service", None)
    if audit_service is None and operation is None:
        return

    request_id = request.headers.get("x-request-id")
    payloads: list[AuditPayloadInput] = []
    if request_payload is not None:
        payloads.append(AuditPayloadInput(kind="request", content_json=request_payload))
    if response_payload is not None:
        payloads.append(AuditPayloadInput(kind="response", content_json=response_payload))

    event = AuditEventInput(
        action=normalize_audit_action(action),
        organization_id=organization_id,
        actor_type=actor_type,
        actor_id=actor_id,
        api_key=api_key,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
        correlation_id=request_id,
        ip=request_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        status=status,
        latency_ms=int((perf_counter() - request_start) * 1000),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        error_type=error.__class__.__name__ if error is not None else None,
        error_code=derive_audit_error_code(error),
        metadata=metadata or {},
        event_id=event_id,
    )
    if operation is not None:
        offer_accounting_audit_content(request, operation, event=event, payloads=payloads)
        return
    await enqueue_audit_event(
        audit_service,
        event,
        payloads=payloads,
        delivery_class=(
            AuditDeliveryClass.REQUIRED if critical else AuditDeliveryClass.BEST_EFFORT
        ),
    )
