from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.auth.roles import Permission
from src.audit.actions import AuditAction
from src.api.admin.endpoints.common import emit_admin_mutation_audit, get_auth_scope
from src.middleware.admin import require_admin_permission
from src.services.email_outbox_service import enqueue_succeeded

router = APIRouter(tags=["Admin Email"])


def _serialize_outbox_record(record) -> dict[str, Any]:  # noqa: ANN001
    return {
        "email_id": record.email_id,
        "kind": record.kind,
        "provider": record.provider,
        "template_key": record.template_key,
        "status": record.status,
        "attempt_count": record.attempt_count,
        "max_attempts": record.max_attempts,
        "recipient_count": len(record.to_addresses) + len(record.cc_addresses) + len(record.bcc_addresses),
        "last_error": (record.last_error or "")[:200] or None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "sent_at": record.sent_at.isoformat() if record.sent_at else None,
    }


@router.get("/ui/api/email/outbox/summary", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def get_email_outbox_summary(request: Request) -> dict[str, Any]:
    repository = getattr(request.app.state, "email_outbox_repository", None)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Email repository unavailable")

    counts = await repository.summarize_status_counts()
    recent = await repository.list_recent(limit=20)
    return {
        "status_counts": {item.status: item.count for item in counts},
        "pending_count": sum(item.count for item in counts if item.status in {"queued", "retrying", "sending"}),
        "recent": [_serialize_outbox_record(record) for record in recent],
    }


@router.post("/ui/api/email/test", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def send_test_email(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    to_address = str(payload.get("to_address") or "").strip()
    provider_override = str(payload.get("provider") or "").strip().lower() or None
    if not to_address or "@" not in to_address:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="valid to_address is required")

    authorization = request.headers.get("Authorization")
    x_master_key = request.headers.get("X-Master-Key")
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.PLATFORM_ADMIN)
    outbox_service = getattr(request.app.state, "email_outbox_service", None)
    if outbox_service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Email service unavailable")
    general_settings = getattr(getattr(request.app.state, "app_config", None), "general_settings", None)
    instance_name = str(getattr(general_settings, "instance_name", "DeltaLLM") or "DeltaLLM")
    default_provider = str(getattr(general_settings, "email_provider", "smtp") or "smtp")
    try:
        queued = await outbox_service.enqueue_template_email(
            template_key="test_email",
            to_addresses=(to_address,),
            payload_json={
                "instance_name": instance_name,
                "provider": provider_override or default_provider,
            },
            kind="test",
            provider_override=provider_override,
            created_by_account_id=scope.account_id,
        )
        if not enqueue_succeeded(queued):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="test email cannot be delivered to the requested recipient",
            )
    except Exception as exc:
        await emit_admin_mutation_audit(
            request=request,
            request_start=request_start,
            action=AuditAction.ADMIN_EMAIL_TEST,
            scope=scope,
            resource_type="email",
            request_payload={"to_address": to_address, "provider": provider_override},
            status="error",
            error=exc,
        )
        raise

    response = {
        "queued": True,
        "email_id": queued.email_id,
        "status": queued.status,
        "to_address": to_address,
        "provider": queued.provider,
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_EMAIL_TEST,
        scope=scope,
        resource_type="email",
        request_payload={"to_address": to_address, "provider": provider_override},
        response_payload=response,
    )
    return response
