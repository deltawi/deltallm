from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request

from src.middleware.auth import require_api_key

router = APIRouter(prefix="/v1", tags=["models"])


@router.get("/models", dependencies=[Depends(require_api_key)])
async def models(request: Request) -> dict[str, object]:
    now = int(time.time())
    model_ids = sorted(request.app.state.model_registry.keys())
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "created": now,
                "owned_by": "deltallm",
            }
            for model_id in model_ids
        ],
    }
