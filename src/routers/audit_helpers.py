from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import Request

from src.services.audit_service import AuditEventInput, AuditPayloadInput, AuditService


def request_client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_hop = forwarded_for.split(",", 1)[0].strip()
        if first_hop:
            return first_hop
    if request.client and request.client.host:
        return request.client.host
    return None


def emit_audit_event(
    *,
    request: Request,
    request_start: float,
    action: str,
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
) -> None:
    audit_service: AuditService | None = getattr(request.app.state, "audit_service", None)
    if audit_service is None:
        return

    request_id = request.headers.get("x-request-id")
    payloads: list[AuditPayloadInput] = []
    if request_payload is not None:
        payloads.append(AuditPayloadInput(kind="request", content_json=request_payload))
    if response_payload is not None:
        payloads.append(AuditPayloadInput(kind="response", content_json=response_payload))

    audit_service.record_event(
        AuditEventInput(
            action=action,
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
            error_code=getattr(getattr(error, "response", None), "status_code", None) if error is not None else None,
            metadata=metadata or {},
        ),
        payloads=payloads,
        critical=critical,
    )
