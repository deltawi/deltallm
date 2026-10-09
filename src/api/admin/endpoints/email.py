from __future__ import annotations

from time import perf_counter
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from src.auth.roles import Permission
from src.audit.actions import AuditAction
from src.api.admin.endpoints.common import emit_admin_mutation_audit, get_auth_scope
from src.middleware.admin import require_admin_permission
from src.db.repositories import AuditRepository
from src.services.email_outbox_service import enqueue_succeeded
from src.services.telemetry_replay import TelemetryReplayService

router = APIRouter(tags=["Admin Email"])


class ResolveUnknownEmailDeliveryRequest(BaseModel):
    resolution: Literal["sent", "failed"]


def _serialize_outbox_record(record) -> dict[str, Any]:  # noqa: ANN001
    return {
        "email_id": record.email_id,
        "kind": record.kind,
        "provider": record.provider,
        "template_key": record.template_key,
        "status": record.status,
        "attempt_count": record.attempt_count,
        "max_attempts": record.max_attempts,
        "recipient_count": len(record.to_addresses)
        + len(record.cc_addresses)
        + len(record.bcc_addresses),
        "last_error": (record.last_error or "")[:200] or None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "sent_at": record.sent_at.isoformat() if record.sent_at else None,
        "delivery_audit_status": record.delivery_audit_status,
        "delivery_audit_attempt_count": record.delivery_audit_attempt_count,
        "delivery_audit_max_attempts": record.delivery_audit_max_attempts,
        "delivery_audit_last_error": (record.delivery_audit_last_error or "")[:200] or None,
    }


@router.get(
    "/ui/api/email/outbox/summary",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def get_email_outbox_summary(request: Request) -> dict[str, Any]:
    repository = getattr(request.app.state, "email_outbox_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Email repository unavailable"
        )

    counts = await repository.summarize_status_counts()
    recent = await repository.list_recent(limit=20)
    return {
        "status_counts": {item.status: item.count for item in counts},
        "pending_count": sum(
            item.count
            for item in counts
            if item.status in {"queued", "retrying", "claimed", "sending"}
        ),
        "delivery_audit_counts": await repository.count_delivery_audits_by_status(),
        "recent": [_serialize_outbox_record(record) for record in recent],
    }


@router.post(
    "/ui/api/email/outbox/{email_id}/delivery-audit/replay",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def replay_blocked_email_delivery_audit(request: Request, email_id: str) -> dict[str, object]:
    request_start = perf_counter()
    scope = get_auth_scope(
        request,
        request.headers.get("Authorization"),
        request.headers.get("X-Master-Key"),
        required_permission=Permission.PLATFORM_ADMIN,
    )
    db = getattr(getattr(request.app.state, "prisma_manager", None), "client", None)
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email repository unavailable",
        )
    response: dict[str, object] = {
        "replayed": True,
        "queue_name": "email_delivery_audit",
        "event_id": email_id,
    }

    async def write_required_audit(repository: AuditRepository) -> None:
        await emit_admin_mutation_audit(
            request=request,
            request_start=request_start,
            action=AuditAction.ADMIN_TELEMETRY_INGESTION_REPLAY,
            scope=scope,
            resource_type="email_delivery_audit",
            resource_id=email_id,
            request_payload={"queue_name": "email_delivery_audit", "event_id": email_id},
            response_payload=response,
            transactional_audit_repository=repository,
        )

    replayed = await TelemetryReplayService(
        db,
        audit_service=getattr(request.app.state, "audit_service", None),
    ).replay_blocked(
        queue_name="email_delivery_audit",
        event_id=email_id,
        replayed_by=scope.account_id or "platform_admin",
        audit_writer=write_required_audit,
    )
    if not replayed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only blocked required email delivery audits can be replayed",
        )
    return response


@router.post(
    "/ui/api/email/outbox/{email_id}/resolve-delivery",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def resolve_unknown_email_delivery(
    request: Request,
    email_id: str,
    payload: ResolveUnknownEmailDeliveryRequest,
) -> dict[str, object]:
    request_start = perf_counter()
    scope = get_auth_scope(
        request,
        request.headers.get("Authorization"),
        request.headers.get("X-Master-Key"),
        required_permission=Permission.PLATFORM_ADMIN,
    )
    db = getattr(getattr(request.app.state, "prisma_manager", None), "client", None)
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email repository unavailable",
        )
    response: dict[str, object] = {
        "resolved": True,
        "email_id": email_id,
        "resolution": payload.resolution,
    }

    async def write_required_audit(repository: AuditRepository) -> None:
        await emit_admin_mutation_audit(
            request=request,
            request_start=request_start,
            action=AuditAction.ADMIN_EMAIL_DELIVERY_RESOLVE,
            scope=scope,
            resource_type="email_delivery",
            resource_id=email_id,
            request_payload={"resolution": payload.resolution},
            response_payload=response,
            metadata={"reason": "ambiguous_delivery_reconciliation"},
            transactional_audit_repository=repository,
        )

    resolved = await TelemetryReplayService(
        db,
        audit_service=getattr(request.app.state, "audit_service", None),
    ).resolve_unknown_email_delivery(
        email_id=email_id,
        resolution=payload.resolution,
        audit_writer=write_required_audit,
    )
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only email deliveries with an unknown outcome can be resolved",
        )
    return response


@router.post(
    "/ui/api/email/test",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def send_test_email(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    to_address = str(payload.get("to_address") or "").strip()
    provider_override = str(payload.get("provider") or "").strip().lower() or None
    if not to_address or "@" not in to_address:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="valid to_address is required"
        )

    authorization = request.headers.get("Authorization")
    x_master_key = request.headers.get("X-Master-Key")
    scope = get_auth_scope(
        request, authorization, x_master_key, required_permission=Permission.PLATFORM_ADMIN
    )
    outbox_service = getattr(request.app.state, "email_outbox_service", None)
    if outbox_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Email service unavailable"
        )
    general_settings = getattr(
        getattr(request.app.state, "app_config", None), "general_settings", None
    )
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
