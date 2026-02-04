"""ProxyLLM - Unified LLM gateway with cost tracking, load balancing, and enterprise features."""

__version__ = "0.1.0"

from deltallm.main import completion, acompletion, embedding, embedding_sync, completion_sync
from deltallm.router import Router, DeploymentConfig, RoutingStrategy
from deltallm.types import (
    CompletionRequest,
    CompletionResponse,
    StreamChunk,
    Message,
    Usage,
)
from deltallm.exceptions import (
    ProxyLLMError,
    AuthenticationError,
    RateLimitError,
    BadRequestError,
    ServiceUnavailableError,
)

__all__ = [
    # Version
    "__version__",
    # Main functions
    "completion",
    "acompletion",
    "completion_sync",
    "embedding",
    "embedding_sync",
    # Router
    "Router",
    "DeploymentConfig",
    "RoutingStrategy",
    # Types
    "CompletionRequest",
    "CompletionResponse",
    "StreamChunk",
    "Message",
    "Usage",
    # Exceptions
    "ProxyLLMError",
    "AuthenticationError",
    "RateLimitError",
    "BadRequestError",
    "ServiceUnavailableError",
]
