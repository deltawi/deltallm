from __future__ import annotations

from time import perf_counter
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from src.api.admin.endpoints.common import (
    emit_admin_mutation_audit,
    get_auth_scope,
    telemetry_db_or_503,
)
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.db.repositories import AuditRepository
from src.middleware.admin import require_admin_permission
from src.services.telemetry_replay import TelemetryReplayService

router = APIRouter(tags=["Admin Telemetry Ingestion"])


@router.post(
    "/ui/api/telemetry-ingestion/{queue_name}/{event_id}/replay",
    dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))],
)
async def replay_blocked_telemetry_event(
    request: Request,
    queue_name: Literal["spend", "audit"],
    event_id: str,
    telemetry_db: object = Depends(telemetry_db_or_503),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, object]:
    request_start = perf_counter()
    scope = get_auth_scope(
        request,
        authorization,
        x_master_key,
        required_permission=Permission.PLATFORM_ADMIN,
    )
    replayed_by = scope.account_id or "platform_admin"
    response: dict[str, object] = {
        "replayed": True,
        "queue_name": queue_name,
        "event_id": event_id,
    }

    async def write_required_audit(transactional_repository: AuditRepository) -> None:
        await emit_admin_mutation_audit(
            request=request,
            request_start=request_start,
            action=AuditAction.ADMIN_TELEMETRY_INGESTION_REPLAY,
            scope=scope,
            resource_type=f"{queue_name}_ingestion_event",
            resource_id=event_id,
            request_payload={"queue_name": queue_name, "event_id": event_id},
            response_payload=response,
            transactional_audit_repository=transactional_repository,
        )

    replayed = await TelemetryReplayService(
        telemetry_db,
        audit_service=getattr(request.app.state, "audit_service", None),
    ).replay_blocked(
        queue_name=queue_name,
        event_id=event_id,
        replayed_by=replayed_by,
        audit_writer=write_required_audit,
    )
    if not replayed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only blocked required telemetry events can be replayed",
        )
    return response
