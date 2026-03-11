from src.api.v1.endpoints.audio_speech import router as audio_speech_router
from src.api.v1.endpoints.audio_transcription import router as audio_transcription_router
from src.api.v1.endpoints.auth import router as auth_router
from src.api.v1.endpoints.batches import router as batches_router
from src.api.v1.endpoints.chat import router as chat_router
from src.api.v1.endpoints.completions import router as completions_router
from src.api.v1.endpoints.embeddings import router as embeddings_router
from src.api.v1.endpoints.files import router as files_router
from src.api.v1.endpoints.health import router as health_router
from src.api.v1.endpoints.images import router as images_router
from src.api.v1.endpoints.metrics import router as metrics_router
from src.api.v1.endpoints.mcp import router as mcp_router
from src.api.v1.endpoints.models import router as models_router
from src.api.v1.endpoints.rerank import router as rerank_router
from src.api.v1.endpoints.responses import router as responses_router
from src.api.v1.endpoints.spend import global_router, spend_router

__all__ = [
    "audio_speech_router",
    "audio_transcription_router",
    "auth_router",
    "batches_router",
    "chat_router",
    "completions_router",
    "embeddings_router",
    "files_router",
    "global_router",
    "health_router",
    "images_router",
    "metrics_router",
    "mcp_router",
    "models_router",
    "rerank_router",
    "responses_router",
    "spend_router",
]
