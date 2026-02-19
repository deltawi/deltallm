from .audio_speech import router as audio_speech_router
from .audio_transcription import router as audio_transcription_router
from .chat import router as chat_router
from .embeddings import router as embeddings_router
from .health import router as health_router
from .images import router as images_router
from .metrics import router as metrics_router
from .models import router as models_router
from .rerank import router as rerank_router

__all__ = [
    "audio_speech_router",
    "audio_transcription_router",
    "chat_router",
    "embeddings_router",
    "health_router",
    "images_router",
    "metrics_router",
    "models_router",
    "rerank_router",
]
