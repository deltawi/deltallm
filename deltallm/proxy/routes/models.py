"""Models routes."""

from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from deltallm.types import ModelList, ModelInfo
from deltallm.router import Router
from deltallm.dynamic_router import DynamicRouter
from deltallm.proxy.dependencies import get_current_user_optional
from deltallm.db.models import User

router = APIRouter(tags=["models"])


def get_static_router(request: Request) -> Router:
    """Get the static router from app state."""
    return request.app.state.router


def get_dynamic_router(request: Request) -> DynamicRouter:
    """Get the dynamic router from app state."""
    return request.app.state.dynamic_router


@router.get("/models")
async def list_models(
    request: Request,
    static_router: Router = Depends(get_static_router),
    dynamic_router: DynamicRouter = Depends(get_dynamic_router),
    current_user: Annotated[Optional[User], Depends(get_current_user_optional)] = None,
    org_id: Optional[UUID] = Query(default=None, description="Filter by organization"),
    model_type: Optional[str] = Query(default=None, description="Filter by model type (chat, embedding, etc.)"),
) -> ModelList:
    """List available models.

    This endpoint is compatible with OpenAI's models API.
    Returns models from both database deployments and static config.
    """
    models = []
    seen_models = set()

    # Get user's org context if available
    effective_org_id = org_id

    # First, get models from database (dynamic router)
    try:
        db_models = await dynamic_router.get_available_models(
            org_id=effective_org_id,
            model_type=model_type,
        )
        for model_name in db_models:
            if model_name not in seen_models:
                models.append(
                    ModelInfo(
                        id=model_name,
                        created=1704067200,  # Placeholder
                        owned_by="deltallm",
                    )
                )
                seen_models.add(model_name)
    except Exception:
        # Database might not be available, continue with static models
        pass

    # Then, add models from static config (if not already present)
    for model_name in static_router.get_available_models():
        if model_name not in seen_models:
            models.append(
                ModelInfo(
                    id=model_name,
                    created=1704067200,  # Placeholder
                    owned_by="deltallm",
                )
            )
            seen_models.add(model_name)

    return ModelList(data=models)


@router.get("/models/{model_id}")
async def get_model(
    model_id: str,
    static_router: Router = Depends(get_static_router),
    dynamic_router: DynamicRouter = Depends(get_dynamic_router),
    current_user: Annotated[Optional[User], Depends(get_current_user_optional)] = None,
) -> ModelInfo:
    """Get information about a specific model.

    This endpoint is compatible with OpenAI's model retrieval API.
    """
    # Check database first
    try:
        db_models = await dynamic_router.get_available_models()
        if model_id in db_models:
            return ModelInfo(
                id=model_id,
                created=1704067200,  # Placeholder
                owned_by="deltallm",
            )
    except Exception:
        pass

    # Check static config
    available = static_router.get_available_models()

    if model_id in available:
        return ModelInfo(
            id=model_id,
            created=1704067200,  # Placeholder
            owned_by="deltallm",
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Model '{model_id}' not found",
    )
