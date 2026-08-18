from __future__ import annotations

from typing import Any
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.middleware.auth import require_api_key
from src.audit.actions import AuditAction
from src.routers.audit_helpers import emit_audit_event

router = APIRouter(prefix="/v1", tags=["batches"])


def _batch_service_or_404(request: Request):
    service = getattr(request.app.state, "batch_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch API is disabled")
    return service


def _batch_create_audit_request_payload(
    *,
    input_file_id: str,
    endpoint: str,
    completion_window: object,
    metadata: dict[str, Any] | None,
    idempotency_key_present: bool,
    webhook_configured: bool,
) -> dict[str, Any]:
    return {
        "input_file_id": input_file_id,
        "endpoint": endpoint,
        "completion_window": completion_window,
        "metadata_present": bool(metadata),
        "idempotency_key_present": idempotency_key_present,
        "webhook_configured": webhook_configured,
    }


def _batch_audit_response_payload(batch: object) -> dict[str, Any] | None:
    if not isinstance(batch, dict):
        return None
    webhook = batch.get("webhook")
    return {
        "id": batch.get("id"),
        "object": batch.get("object"),
        "endpoint": batch.get("endpoint"),
        "status": batch.get("status"),
        "input_file_id": batch.get("input_file_id"),
        "output_file_id": batch.get("output_file_id"),
        "error_file_id": batch.get("error_file_id"),
        "request_counts": batch.get("request_counts"),
        "metadata_present": bool(batch.get("metadata")),
        "webhook_configured": bool(isinstance(webhook, dict) and webhook.get("configured") is True),
    }


@router.post("/batches", dependencies=[Depends(require_api_key)])
async def create_batch(request: Request, payload: dict[str, Any]):
    request_start = perf_counter()
    auth = request.state.user_api_key
    input_file_id = str(payload.get("input_file_id") or "").strip()
    endpoint = str(payload.get("endpoint") or "").strip()
    completion_window = payload.get("completion_window")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None
    webhook = payload.get("webhook")
    webhook_configured = webhook is not None
    idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip() or None
    audit_request_payload = _batch_create_audit_request_payload(
        input_file_id=input_file_id,
        endpoint=endpoint,
        completion_window=completion_window,
        metadata=metadata,
        idempotency_key_present=bool(idempotency_key),
        webhook_configured=webhook_configured,
    )
    try:
        service = _batch_service_or_404(request)
        audit_metadata = {
            "route": request.url.path,
            "idempotency_key_present": bool(idempotency_key),
            "webhook_configured": webhook_configured,
        }
        create_batch_result = (
            getattr(service, "create_batch_result", None) or service.create_embeddings_batch_result
        )
        result = await create_batch_result(
            auth=auth,
            input_file_id=input_file_id,
            endpoint=endpoint,
            metadata=metadata,
            completion_window=completion_window,
            idempotency_key=idempotency_key,
            webhook=webhook,
        )
        created = result.response
        audit_metadata.update(result.audit_metadata)
        await emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.BATCH_CREATE_REQUEST,
            status="success",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="batch",
            resource_id=created.get("id") if isinstance(created, dict) else None,
            request_payload=audit_request_payload,
            response_payload=_batch_audit_response_payload(created),
            metadata=audit_metadata,
        )
        return created
    except Exception as exc:
        await emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.BATCH_CREATE_REQUEST,
            status="error",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="batch",
            request_payload=audit_request_payload,
            error=exc,
            metadata={
                "route": request.url.path,
                "idempotency_key_present": bool(idempotency_key),
                "webhook_configured": webhook_configured,
            },
        )
        raise


@router.get("/batches/{batch_id}", dependencies=[Depends(require_api_key)])
async def get_batch(request: Request, batch_id: str):
    request_start = perf_counter()
    auth = request.state.user_api_key
    try:
        service = _batch_service_or_404(request)
        batch = await service.get_batch(batch_id=batch_id, auth=auth)
        await emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.BATCH_READ_REQUEST,
            status="success",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="batch",
            resource_id=batch_id,
            request_payload={"batch_id": batch_id},
            response_payload=_batch_audit_response_payload(batch),
            metadata={"route": request.url.path},
        )
        return batch
    except Exception as exc:
        await emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.BATCH_READ_REQUEST,
            status="error",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="batch",
            resource_id=batch_id,
            request_payload={"batch_id": batch_id},
            error=exc,
            metadata={"route": request.url.path},
        )
        raise


@router.get("/batches", dependencies=[Depends(require_api_key)])
async def list_batches(request: Request, limit: int = 20):
    request_start = perf_counter()
    auth = request.state.user_api_key
    try:
        service = _batch_service_or_404(request)
        batches = await service.list_batches(auth=auth, limit=limit)
        await emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.BATCH_LIST_REQUEST,
            status="success",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="batch",
            request_payload={"limit": limit},
            response_payload={"count": len(batches) if isinstance(batches, list) else None},
            metadata={"route": request.url.path},
        )
        return batches
    except Exception as exc:
        await emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.BATCH_LIST_REQUEST,
            status="error",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="batch",
            request_payload={"limit": limit},
            error=exc,
            metadata={"route": request.url.path},
        )
        raise


@router.post("/batches/{batch_id}/cancel", dependencies=[Depends(require_api_key)])
async def cancel_batch(request: Request, batch_id: str):
    request_start = perf_counter()
    auth = request.state.user_api_key
    try:
        service = _batch_service_or_404(request)
        result = await service.cancel_batch(batch_id=batch_id, auth=auth)
        await emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.BATCH_CANCEL_REQUEST,
            status="success",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="batch",
            resource_id=batch_id,
            request_payload={"batch_id": batch_id},
            response_payload=_batch_audit_response_payload(result),
            metadata={"route": request.url.path},
        )
        return result
    except Exception as exc:
        await emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.BATCH_CANCEL_REQUEST,
            status="error",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="batch",
            resource_id=batch_id,
            request_payload={"batch_id": batch_id},
            error=exc,
            metadata={"route": request.url.path},
        )
        raise
