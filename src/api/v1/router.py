from fastapi import APIRouter

from src.api.v1.endpoints import (
    auth_router,
    chat_router,
    embeddings_router,
    global_router,
    health_router,
    metrics_router,
    models_router,
    spend_router,
)

v1_router = APIRouter()

# Preserve existing route registration order and paths.
v1_router.include_router(health_router)
v1_router.include_router(metrics_router)
v1_router.include_router(chat_router)
v1_router.include_router(embeddings_router)
v1_router.include_router(models_router)
v1_router.include_router(spend_router)
v1_router.include_router(global_router)
v1_router.include_router(auth_router)

__all__ = ["v1_router"]
