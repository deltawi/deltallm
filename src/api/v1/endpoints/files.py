from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response

from src.batch.access import can_access_owned_resource
from src.middleware.auth import require_api_key
from src.audit.actions import AuditAction
from src.routers.audit_helpers import emit_audit_event

router = APIRouter(prefix="/v1", tags=["files"])


def _batch_service_or_404(request: Request):
    service = getattr(request.app.state, "batch_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch files API is disabled")
    return service


@router.post("/files", dependencies=[Depends(require_api_key)])
async def create_file(
    request: Request,
    file: UploadFile = File(...),
    purpose: str = "batch",
):
    request_start = perf_counter()
    auth = request.state.user_api_key
    try:
        service = _batch_service_or_404(request)
        created = await service.create_file(auth=auth, upload=file, purpose=purpose)
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.FILE_CREATE_REQUEST,
            status="success",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="file",
            resource_id=created.get("id") if isinstance(created, dict) else None,
            request_payload={"purpose": purpose, "filename": file.filename, "content_type": file.content_type},
            response_payload=created if isinstance(created, dict) else None,
            metadata={"route": request.url.path},
        )
        return created
    except Exception as exc:
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.FILE_CREATE_REQUEST,
            status="error",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="file",
            request_payload={"purpose": purpose, "filename": file.filename, "content_type": file.content_type},
            error=exc,
            metadata={"route": request.url.path},
        )
        raise


@router.get("/files/{file_id}", dependencies=[Depends(require_api_key)])
async def get_file(request: Request, file_id: str):
    request_start = perf_counter()
    auth = request.state.user_api_key
    try:
        _batch_service_or_404(request)
        record = await request.app.state.batch_repository.get_file(file_id)
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
        if not can_access_owned_resource(
            owner_api_key=record.created_by_api_key,
            owner_team_id=record.created_by_team_id,
            owner_organization_id=getattr(record, "created_by_organization_id", None),
            auth=auth,
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="File access denied")
        response = request.app.state.batch_service.file_to_response(record)
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.FILE_READ_REQUEST,
            status="success",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="file",
            resource_id=file_id,
            request_payload={"file_id": file_id},
            response_payload=response if isinstance(response, dict) else None,
            metadata={"route": request.url.path},
        )
        return response
    except Exception as exc:
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.FILE_READ_REQUEST,
            status="error",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="file",
            resource_id=file_id,
            request_payload={"file_id": file_id},
            error=exc,
            metadata={"route": request.url.path},
        )
        raise


@router.get("/files/{file_id}/content", dependencies=[Depends(require_api_key)])
async def get_file_content(request: Request, file_id: str):
    request_start = perf_counter()
    auth = request.state.user_api_key
    try:
        service = _batch_service_or_404(request)
        content = await service.get_file_content(file_id=file_id, auth=auth)
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.FILE_CONTENT_READ_REQUEST,
            status="success",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="file",
            resource_id=file_id,
            request_payload={"file_id": file_id},
            response_payload={"size_bytes": len(content)},
            metadata={"route": request.url.path},
        )
        return Response(content=content, media_type="application/jsonl")
    except Exception as exc:
        emit_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.FILE_CONTENT_READ_REQUEST,
            status="error",
            actor_type="api_key",
            actor_id=auth.user_id or auth.api_key,
            organization_id=getattr(auth, "organization_id", None),
            api_key=auth.api_key,
            resource_type="file",
            resource_id=file_id,
            request_payload={"file_id": file_id},
            error=exc,
            metadata={"route": request.url.path},
        )
        raise
