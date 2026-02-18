from src.api.v1.endpoints.auth import router as auth_router
from src.api.v1.endpoints.chat import router as chat_router
from src.api.v1.endpoints.embeddings import router as embeddings_router
from src.api.v1.endpoints.health import router as health_router
from src.api.v1.endpoints.metrics import router as metrics_router
from src.api.v1.endpoints.models import router as models_router
from src.api.v1.endpoints.spend import global_router, spend_router

__all__ = [
    "auth_router",
    "chat_router",
    "embeddings_router",
    "global_router",
    "health_router",
    "metrics_router",
    "models_router",
    "spend_router",
]
