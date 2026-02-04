"""Routes for the proxy server."""

from .audio import router as audio_router
from .audit import router as audit_router
from .auth import router as auth_router
from .batches import router as batches_router
from .budget import router as budget_router
from .chat import router as chat_router
from .deployments import router as deployments_router
from .embeddings import router as embeddings_router
from .files import router as files_router
from .guardrails import router as guardrails_router
from .models import router as models_router
from .keys import router as keys_router
from .health import router as health_router
from .organizations import router as org_router
from .pricing import router as pricing_router
from .providers import router as providers_router
from .teams import router as team_router

__all__ = [
    "audio_router",
    "audit_router",
    "auth_router",
    "batches_router",
    "budget_router",
    "chat_router",
    "deployments_router",
    "embeddings_router",
    "files_router",
    "guardrails_router",
    "models_router",
    "keys_router",
    "health_router",
    "org_router",
    "pricing_router",
    "providers_router",
    "team_router",
]
