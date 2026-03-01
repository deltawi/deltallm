from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response

from src.batch.access import can_access_owned_resource
from src.middleware.auth import require_api_key

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
    auth = request.state.user_api_key
    service = _batch_service_or_404(request)
    return await service.create_file(auth=auth, upload=file, purpose=purpose)


@router.get("/files/{file_id}", dependencies=[Depends(require_api_key)])
async def get_file(request: Request, file_id: str):
    auth = request.state.user_api_key
    _batch_service_or_404(request)
    record = await request.app.state.batch_repository.get_file(file_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    if not can_access_owned_resource(
        owner_api_key=record.created_by_api_key,
        owner_team_id=record.created_by_team_id,
        auth=auth,
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="File access denied")
    return request.app.state.batch_service.file_to_response(record)


@router.get("/files/{file_id}/content", dependencies=[Depends(require_api_key)])
async def get_file_content(request: Request, file_id: str):
    auth = request.state.user_api_key
    service = _batch_service_or_404(request)
    content = await service.get_file_content(file_id=file_id, auth=auth)
    return Response(content=content, media_type="application/jsonl")
