"""Common type definitions shared across the SDK."""

from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class ModelType(str, Enum):
    """Model type classification for routing and capability matching."""

    CHAT = "chat"
    EMBEDDING = "embedding"
    IMAGE_GENERATION = "image_generation"
    AUDIO_TRANSCRIPTION = "audio_transcription"  # STT
    AUDIO_SPEECH = "audio_speech"  # TTS
    RERANK = "rerank"
    MODERATION = "moderation"

    @classmethod
    def get_endpoint_type(cls, model_type: "ModelType") -> str:
        """Map model type to spend tracking endpoint_type."""
        mapping = {
            cls.CHAT: "chat",
            cls.EMBEDDING: "embedding",
            cls.IMAGE_GENERATION: "image",
            cls.AUDIO_TRANSCRIPTION: "audio_transcription",
            cls.AUDIO_SPEECH: "audio_speech",
            cls.RERANK: "rerank",
            cls.MODERATION: "moderation",
        }
        return mapping.get(model_type, "chat")

    @classmethod
    def values(cls) -> list[str]:
        """Return all valid model type values."""
        return [t.value for t in cls]


class ErrorDetail(BaseModel):
    """Error detail information."""
    
    message: str
    type: Optional[str] = None
    param: Optional[str] = None
    code: Optional[str] = None


class ErrorResponse(BaseModel):
    """OpenAI-compatible error response."""
    
    error: ErrorDetail


class FunctionCall(BaseModel):
    """Function call details."""
    
    name: str
    arguments: str


class ToolCall(BaseModel):
    """Tool call in a message."""
    
    id: str
    type: Literal["function"] = "function"
    function: FunctionCall


class TopLogProb(BaseModel):
    """Top log probability information."""
    
    token: str
    logprob: float
    bytes: Optional[list[int]] = None


class LogProbs(BaseModel):
    """Log probabilities for tokens."""
    
    content: Optional[list[dict[str, Any]]] = None
    refusal: Optional[list[dict[str, Any]]] = None


class FinishReason(str):
    """Possible finish reasons."""
    
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"
    FUNCTION_CALL = "function_call"
