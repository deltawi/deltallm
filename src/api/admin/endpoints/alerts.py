from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.api.admin.endpoints.common import emit_admin_mutation_audit, get_auth_scope
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.middleware.admin import require_admin_permission
from src.notifications.types import NotificationMessage
from src.services.email.notification_recipients import NotificationRecipients

router = APIRouter(tags=["Admin Alerts"])


@router.get(
    "/ui/api/alerts/destinations",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def list_alert_destinations(request: Request) -> dict[str, Any]:
    channels = getattr(request.app.state, "alert_destination_channels", None) or {}
    return {
        "destinations": [
            {
                "name": channel.name,
                "type": channel.type,
                "alert_types": sorted(channel.settings.alert_types),
            }
            for channel in channels.values()
        ]
    }


@router.post(
    "/ui/api/alerts/test",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def send_test_alert(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    destination = str(payload.get("destination") or "").strip()
    channels = getattr(request.app.state, "alert_destination_channels", None) or {}
    channel = channels.get(destination)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="alert destination not found"
        )
    scope = get_auth_scope(
        request,
        request.headers.get("Authorization"),
        request.headers.get("X-Master-Key"),
        required_permission=Permission.PLATFORM_ADMIN,
    )
    general_settings = getattr(
        getattr(request.app.state, "app_config", None), "general_settings", None
    )
    instance_name = str(getattr(general_settings, "instance_name", "DeltaLLM") or "DeltaLLM")
    # Sent straight to one destination: no routing, dedupe, or recipient lookup.
    result = await channel.send(
        message=NotificationMessage(
            alert_type="alert_test",
            metric_kind="alert_test",
            payload={
                "instance_name": instance_name,
                "destination": destination,
                "sent_at": datetime.now(tz=UTC).isoformat(),
            },
            created_by_account_id=scope.account_id,
        ),
        recipients=NotificationRecipients(emails=(), policy="destination_test"),
    )
    delivered = result.outcome == "queued"
    response = {
        "delivered": delivered,
        "destination": destination,
        "type": channel.type,
        "status_code": (result.detail or {}).get("status_code"),
        "error": result.error,
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ALERT_TEST,
        scope=scope,
        resource_type="alert_destination",
        resource_id=destination,
        request_payload={"destination": destination},
        response_payload=response,
        status="success" if delivered else "error",
    )
    return response
