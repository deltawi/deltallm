from __future__ import annotations

from time import perf_counter
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.auth.roles import Permission
from src.audit.actions import AuditAction
from src.api.admin.endpoints.common import emit_admin_mutation_audit, get_auth_scope
from src.middleware.admin import require_admin_permission
from src.services.email_feedback_service import EmailFeedbackError

router = APIRouter(tags=["Email Feedback"])


def _serialize_suppression(record) -> dict[str, Any]:  # noqa: ANN001
    return {
        "email_address": record.email_address,
        "provider": record.provider,
        "reason": record.reason,
        "source": record.source,
        "provider_message_id": record.provider_message_id,
        "webhook_event_id": record.webhook_event_id,
        "metadata": record.metadata,
        "first_seen_at": record.first_seen_at.isoformat() if record.first_seen_at else None,
        "last_seen_at": record.last_seen_at.isoformat() if record.last_seen_at else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


@router.post("/webhooks/email/resend")
async def handle_resend_email_webhook(request: Request) -> dict[str, Any]:
    service = getattr(request.app.state, "email_feedback_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Email feedback service unavailable")
    raw_body = await request.body()
    try:
        outcome = await service.handle_resend_webhook(headers=request.headers, raw_body=raw_body)
    except EmailFeedbackError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {
        "ok": True,
        "provider": outcome.provider,
        "event_type": outcome.event_type,
        "duplicate": outcome.duplicate,
        "suppressed_count": outcome.suppressed_count,
        "recipient_addresses": list(outcome.recipient_addresses),
        "email_id": outcome.email_id,
    }


@router.get("/ui/api/email/suppressions", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def list_email_suppressions(request: Request, search: str | None = None, limit: int = 100) -> dict[str, Any]:
    repository = getattr(request.app.state, "email_feedback_repository", None)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Email feedback repository unavailable")
    records = await repository.list_suppressions(limit=limit, search=search)
    return {"data": [_serialize_suppression(record) for record in records], "count": len(records)}


@router.delete("/ui/api/email/suppressions/{email_address}", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def delete_email_suppression(request: Request, email_address: str) -> dict[str, bool]:
    request_start = perf_counter()
    repository = getattr(request.app.state, "email_feedback_repository", None)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Email feedback repository unavailable")
    normalized_email = unquote(email_address).strip().lower()
    if not normalized_email or "@" not in normalized_email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="valid email_address is required")

    authorization = request.headers.get("Authorization")
    x_master_key = request.headers.get("X-Master-Key")
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.PLATFORM_ADMIN)

    deleted = await repository.remove_suppression(normalized_email)
    response = {"deleted": deleted}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_EMAIL_SUPPRESSION_DELETE,
        scope=scope,
        resource_type="email_suppression",
        resource_id=normalized_email,
        response_payload=response,
    )
    return response
