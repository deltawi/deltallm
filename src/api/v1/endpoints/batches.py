from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.middleware.auth import require_api_key

router = APIRouter(prefix="/v1", tags=["batches"])


def _batch_service_or_404(request: Request):
    service = getattr(request.app.state, "batch_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch API is disabled")
    return service


@router.post("/batches", dependencies=[Depends(require_api_key)])
async def create_batch(request: Request, payload: dict[str, Any]):
    auth = request.state.user_api_key
    input_file_id = str(payload.get("input_file_id") or "").strip()
    endpoint = str(payload.get("endpoint") or "").strip()
    completion_window = payload.get("completion_window")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None
    service = _batch_service_or_404(request)
    return await service.create_embeddings_batch(
        auth=auth,
        input_file_id=input_file_id,
        endpoint=endpoint,
        metadata=metadata,
        completion_window=completion_window,
    )


@router.get("/batches/{batch_id}", dependencies=[Depends(require_api_key)])
async def get_batch(request: Request, batch_id: str):
    auth = request.state.user_api_key
    service = _batch_service_or_404(request)
    return await service.get_batch(batch_id=batch_id, auth=auth)


@router.get("/batches", dependencies=[Depends(require_api_key)])
async def list_batches(request: Request, limit: int = 20):
    auth = request.state.user_api_key
    service = _batch_service_or_404(request)
    return await service.list_batches(auth=auth, limit=limit)


@router.post("/batches/{batch_id}/cancel", dependencies=[Depends(require_api_key)])
async def cancel_batch(request: Request, batch_id: str):
    auth = request.state.user_api_key
    service = _batch_service_or_404(request)
    return await service.cancel_batch(batch_id=batch_id, auth=auth)
