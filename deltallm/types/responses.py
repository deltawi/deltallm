"""Response type definitions."""

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

from .common import FinishReason, LogProbs, ToolCall


class Usage(BaseModel):
    """Token usage information."""
    
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    
    # Extended fields for some providers
    prompt_tokens_details: Optional[dict[str, Any]] = None
    completion_tokens_details: Optional[dict[str, Any]] = None
    
    # Anthropic prompt caching
    cache_creation_input_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None


class CompletionChoice(BaseModel):
    """A single completion choice."""
    
    index: int
    message: dict[str, Any]
    finish_reason: Optional[str] = None
    logprobs: Optional[LogProbs] = None


class CompletionResponse(BaseModel):
    """Chat completion response following OpenAI API format."""
    
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[CompletionChoice]
    usage: Usage
    system_fingerprint: Optional[str] = None
    
    # Hidden params for ProxyLLM internal use
    _hidden_params: Optional[dict[str, Any]] = None


class DeltaMessage(BaseModel):
    """Delta message in a streaming chunk."""
    
    role: Optional[str] = None
    content: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None
    function_call: Optional[dict[str, Any]] = None
    refusal: Optional[str] = None


class StreamChoice(BaseModel):
    """Choice in a streaming chunk."""
    
    index: int
    delta: DeltaMessage
    finish_reason: Optional[str] = None
    logprobs: Optional[LogProbs] = None


class StreamChunk(BaseModel):
    """Streaming completion chunk."""
    
    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: list[StreamChoice]
    usage: Optional[Usage] = None
    system_fingerprint: Optional[str] = None


class Embedding(BaseModel):
    """Single embedding result."""
    
    object: Literal["embedding"] = "embedding"
    embedding: list[float]
    index: int


class EmbeddingResponse(BaseModel):
    """Embedding response following OpenAI API format."""
    
    object: Literal["list"] = "list"
    data: list[Embedding]
    model: str
    usage: Usage


class ImageData(BaseModel):
    """Generated image data."""
    
    url: Optional[str] = None
    b64_json: Optional[str] = None
    revised_prompt: Optional[str] = None


class ImageGenerationResponse(BaseModel):
    """Image generation response."""
    
    created: int
    data: list[ImageData]


class ModelPermission(BaseModel):
    """Model permission information."""
    
    id: str
    object: str = "model_permission"
    created: int
    allow_create_engine: bool = False
    allow_sampling: bool = True
    allow_logprobs: bool = True
    allow_search_indices: bool = False
    allow_view: bool = True
    allow_fine_tuning: bool = False
    organization: str = "*"
    group: Optional[str] = None
    is_blocking: bool = False


class ModelInfo(BaseModel):
    """Model information."""
    
    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str
    permission: list[ModelPermission] = Field(default_factory=list)
    root: Optional[str] = None
    parent: Optional[str] = None
    
    # ProxyLLM-specific fields
    max_tokens: Optional[int] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    supports_vision: bool = False
    supports_tools: bool = False
    supports_streaming: bool = True
    provider: Optional[str] = None


class ModelList(BaseModel):
    """List of available models."""
    
    object: Literal["list"] = "list"
    data: list[ModelInfo]
