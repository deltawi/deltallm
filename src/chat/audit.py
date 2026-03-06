from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import Request

from src.audit.actions import AuditAction
from src.services.audit_service import AuditEventInput, AuditPayloadInput, AuditService


def audit_action_for_path(path: str) -> str:
    if path == "/v1/chat/completions":
        return "CHAT_COMPLETION_REQUEST"
    if path == "/v1/completions":
        return "COMPLETION_REQUEST"
    if path == "/v1/responses":
        return "RESPONSES_REQUEST"
    return "TEXT_GENERATION_REQUEST"


def request_client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_hop = forwarded_for.split(",", 1)[0].strip()
        if first_hop:
            return first_hop
    if request.client and request.client.host:
        return request.client.host
    return None


def emit_text_audit_event(
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
    audit_service: AuditService | None = getattr(request.app.state, "audit_service", None)
    if audit_service is None:
        return

    request_id = request.headers.get("x-request-id")
    payloads = [
        AuditPayloadInput(kind="request", content_json=request_data),
        AuditPayloadInput(kind="response", content_json=response_data),
    ]
    if response_data is None:
        payloads = [AuditPayloadInput(kind="request", content_json=request_data)]
    audit_service.record_event(
        AuditEventInput(
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
            error_code=getattr(getattr(error, "response", None), "status_code", None) if error is not None else None,
            metadata=metadata or {},
        ),
        payloads=payloads,
        critical=True,
    )


def emit_prompt_resolution_audit_event(
    *,
    request: Request,
    auth: Any,
    status: str,
    request_start: float,
    prompt_key: str | None,
    metadata: dict[str, Any] | None = None,
    error: Exception | None = None,
) -> None:
    audit_service: AuditService | None = getattr(request.app.state, "audit_service", None)
    if audit_service is None:
        return

    request_id = request.headers.get("x-request-id")
    audit_service.record_event(
        AuditEventInput(
            action=AuditAction.PROMPT_RESOLUTION_REQUEST.value,
            organization_id=getattr(auth, "organization_id", None),
            actor_type="api_key",
            actor_id=getattr(auth, "user_id", None) or getattr(auth, "api_key", None),
            api_key=getattr(auth, "api_key", None),
            resource_type="prompt",
            resource_id=prompt_key,
            request_id=request_id,
            correlation_id=request_id,
            ip=request_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            status=status,
            latency_ms=int((perf_counter() - request_start) * 1000),
            error_type=error.__class__.__name__ if error is not None else None,
            error_code=getattr(getattr(error, "response", None), "status_code", None) if error is not None else None,
            metadata=metadata or {},
        ),
        critical=False,
    )
