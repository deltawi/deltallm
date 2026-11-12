from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import Request

from src.audit.delivery import AuditDeliveryClass
from src.audit.errors import derive_audit_error_code
from src.client_address import request_client_ip
from src.chat.stream_reply import AUDIT_REPLY_MEMORY, StreamReplyAccumulator
from src.routers.audit_helpers import (
    accounting_audit_content_wanted,
    accounting_operation,
    offer_accounting_audit_content,
)
from src.services.audit.audit_service import (
    AuditEventInput,
    AuditPayloadInput,
    AuditService,
    enqueue_audit_event,
)
from src.services.audit.payload_compaction import DEFAULT_AUDIT_PAYLOAD_MAX_BYTES


# Room for the fields around the reply text (ids, model, usage, JSON keys), so a
# reply at the limit is cut and marked ``truncated`` and not replaced as a whole
# when the payload is compacted.
_REPLY_HEADROOM_BYTES = 16 * 1024


def stream_reply_accumulator(request: Request, auth: Any) -> StreamReplyAccumulator | None:
    """Return a bounded reply accumulator when the reply of this stream can be stored.

    It returns None when no text audit event follows, or when the pod already
    knows that the organization has content storage off. Those streams keep no
    reply in memory.
    """
    organization_id = getattr(auth, "organization_id", None)
    audit_service: AuditService | None
    if accounting_operation(request) is not None:
        # Accounting v2: the reply goes to the content buffer, see emit_text_audit_event.
        if not accounting_audit_content_wanted(request, organization_id):
            return None
        audit_service = request.app.state.audit_content_buffer.audit_service
    else:
        audit_service = getattr(request.app.state, "audit_service", None)
        if audit_service is None:
            return None
        if audit_service.peek_content_storage_policy(organization_id) is False:
            return None
    config = getattr(audit_service, "ingestion_config", None)
    max_bytes = int(getattr(config, "payload_max_bytes", DEFAULT_AUDIT_PAYLOAD_MAX_BYTES))
    headroom = min(_REPLY_HEADROOM_BYTES, max_bytes // 2)
    return StreamReplyAccumulator(max_chars=max(1, max_bytes - headroom), memory=AUDIT_REPLY_MEMORY)


def audit_action_for_path(path: str) -> str:
    if path == "/v1/chat/completions":
        return "CHAT_COMPLETION_REQUEST"
    if path == "/v1/completions":
        return "COMPLETION_REQUEST"
    if path == "/v1/responses":
        return "RESPONSES_REQUEST"
    if path == "/v1/messages":
        return "MESSAGES_REQUEST"
    return "TEXT_GENERATION_REQUEST"


async def emit_text_audit_event(
    *,
    request: Request,
    auth: Any,
    action: str,
    model: str,
    status: str,
    request_start: float,
    request_data: dict[str, Any] | None,
    response_data: dict[str, Any] | None,
    error: Exception | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    # With accounting v2 the accounting write records the audit event; only the
    # bodies go to the best-effort content buffer, without waiting.
    operation = accounting_operation(request)
    if operation is not None and not accounting_audit_content_wanted(
        request, getattr(auth, "organization_id", None)
    ):
        return
    audit_service: AuditService | None = getattr(request.app.state, "audit_service", None)
    if audit_service is None and operation is None:
        return

    request_id = request.headers.get("x-request-id")
    payloads = [
        AuditPayloadInput(kind="request", content_json=request_data),
        AuditPayloadInput(kind="response", content_json=response_data),
    ]
    if response_data is None:
        payloads = [AuditPayloadInput(kind="request", content_json=request_data)]
    event = AuditEventInput(
        action=action,
        organization_id=getattr(auth, "organization_id", None),
        actor_type="api_key",
        actor_id=getattr(auth, "user_id", None) or getattr(auth, "api_key", None),
        api_key=getattr(auth, "api_key", None),
        resource_type="model",
        resource_id=model,
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
    )
    if operation is not None:
        offer_accounting_audit_content(request, operation, event=event, payloads=payloads)
        return
    await enqueue_audit_event(
        audit_service,
        event,
        payloads=payloads,
        delivery_class=AuditDeliveryClass.REQUIRED,
    )
