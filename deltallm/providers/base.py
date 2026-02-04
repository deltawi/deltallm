"""Base provider interface for ProxyLLM."""

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Optional, TypeVar
from dataclasses import dataclass, field

from deltallm.types import (
    CompletionRequest,
    CompletionResponse,
    StreamChunk,
    EmbeddingRequest,
    EmbeddingResponse,
    ModelInfo as ModelInfoType,
)
from deltallm.types.common import ModelType


@dataclass
class ProviderCapabilities:
    """Provider capabilities."""

    chat: bool = True
    streaming: bool = True
    embeddings: bool = False
    images: bool = False
    audio: bool = False
    tools: bool = False
    vision: bool = False
    json_mode: bool = False
    # Model type classification support
    supported_model_types: list[str] = field(
        default_factory=lambda: [ModelType.CHAT.value]
    )

    def supports_type(self, model_type: str) -> bool:
        """Check if provider supports a given model type."""
        return model_type in self.supported_model_types


@dataclass
class ModelInfo:
    """Provider model information."""
    
    id: str
    name: str
    max_tokens: int
    max_input_tokens: int = 0
    max_output_tokens: int = 0
    input_cost_per_token: float = 0.0
    output_cost_per_token: float = 0.0
    supports_vision: bool = False
    supports_tools: bool = False
    supports_streaming: bool = True
    provider: str = ""
    mode: str = "chat"


class BaseProvider(ABC):
    """Base class for all LLM provider adapters."""
    
    provider_name: str = ""
    capabilities: ProviderCapabilities = ProviderCapabilities()
    
    def __init__(self, api_key: Optional[str] = None, api_base: Optional[str] = None) -> None:
        """Initialize the provider.
        
        Args:
            api_key: API key for the provider
            api_base: Optional custom API base URL
        """
        self.api_key = api_key
        self.api_base = api_base
    
    @abstractmethod
    async def chat_completion(
        self,
        request: CompletionRequest,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> CompletionResponse:
        """Execute a chat completion request.
        
        Args:
            request: The completion request
            api_key: Optional API key override
            api_base: Optional API base URL override
            
        Returns:
            Completion response
        """
        pass
    
    @abstractmethod
    async def chat_completion_stream(
        self,
        request: CompletionRequest,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        """Execute a streaming chat completion request.
        
        Args:
            request: The completion request
            api_key: Optional API key override
            api_base: Optional API base URL override
            
        Yields:
            Stream chunks
        """
        pass
    
    async def embedding(
        self,
        request: EmbeddingRequest,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> EmbeddingResponse:
        """Execute an embedding request.
        
        Args:
            request: The embedding request
            api_key: Optional API key override
            api_base: Optional API base URL override
            
        Returns:
            Embedding response
            
        Raises:
            NotImplementedError: If embeddings are not supported
        """
        raise NotImplementedError(f"Provider {self.provider_name} does not support embeddings")
    
    @abstractmethod
    def transform_request(self, request: CompletionRequest) -> dict[str, Any]:
        """Transform OpenAI-style request to provider format.
        
        Args:
            request: The completion request
            
        Returns:
            Provider-specific request body
        """
        pass
    
    @abstractmethod
    def transform_response(self, response: dict[str, Any], model: str) -> CompletionResponse:
        """Transform provider response to OpenAI format.
        
        Args:
            response: The provider response
            model: The model name
            
        Returns:
            OpenAI-style completion response
        """
        pass
    
    @abstractmethod
    def transform_stream_chunk(self, chunk: dict[str, Any], model: str) -> Optional[StreamChunk]:
        """Transform provider stream chunk to OpenAI format.
        
        Args:
            chunk: The provider chunk
            model: The model name
            
        Returns:
            OpenAI-style stream chunk, or None if chunk should be skipped
        """
        pass
    
    @abstractmethod
    def get_model_info(self, model: str) -> ModelInfo:
        """Get information about a model.
        
        Args:
            model: The model name
            
        Returns:
            Model information
        """
        pass
    
    def supports_model(self, model: str) -> bool:
        """Check if this provider supports the given model.
        
        Args:
            model: The model name
            
        Returns:
            True if the model is supported
        """
        try:
            info = self.get_model_info(model)
            return info is not None
        except Exception:
            return False
    
    def get_headers(self, api_key: Optional[str] = None) -> dict[str, str]:
        """Get authentication headers.
        
        Args:
            api_key: Optional API key override
            
        Returns:
            Headers dictionary
        """
        key = api_key or self.api_key
        if not key:
            return {}
        return {"Authorization": f"Bearer {key}"}
