"""Request type definitions."""

from typing import Any, Literal, Optional, Union
from pydantic import BaseModel, Field, model_validator

from .messages import Message


ToolChoice = Union[Literal["none", "auto", "required"], dict[str, Any]]


class Tool(BaseModel):
    """Tool definition for function calling."""
    
    type: Literal["function"] = "function"
    function: dict[str, Any]


class ResponseFormat(BaseModel):
    """Response format specification (e.g., JSON mode)."""
    
    type: Literal["text", "json_object", "json_schema"] = "text"
    json_schema: Optional[dict[str, Any]] = None
    
    @model_validator(mode="after")
    def validate_schema(self) -> "ResponseFormat":
        """Validate JSON schema when type is json_schema."""
        if self.type == "json_schema" and not self.json_schema:
            raise ValueError("json_schema must be provided when type='json_schema'")
        return self


class CompletionRequest(BaseModel):
    """Chat completion request following OpenAI API format."""
    
    model: str
    messages: list[Message]
    temperature: Optional[float] = Field(default=1.0, ge=0, le=2)
    top_p: Optional[float] = Field(default=1.0, ge=0, le=1)
    n: Optional[int] = Field(default=1, ge=1, le=128)
    stream: Optional[bool] = False
    stop: Optional[Union[str, list[str]]] = None
    max_tokens: Optional[int] = Field(default=None, ge=1)
    max_completion_tokens: Optional[int] = Field(default=None, ge=1)
    presence_penalty: Optional[float] = Field(default=0, ge=-2, le=2)
    frequency_penalty: Optional[float] = Field(default=0, ge=-2, le=2)
    logit_bias: Optional[dict[str, int]] = None
    user: Optional[str] = None
    response_format: Optional[ResponseFormat] = None
    seed: Optional[int] = None
    tools: Optional[list[Tool]] = None
    tool_choice: Optional[ToolChoice] = None
    parallel_tool_calls: Optional[bool] = True
    
    # Additional metadata for ProxyLLM
    metadata: Optional[dict[str, Any]] = None
    timeout: Optional[float] = None
    
    model_config = {"extra": "allow"}
    
    @model_validator(mode="after")
    def validate_request(self) -> "CompletionRequest":
        """Validate completion request."""
        # Check that max_tokens and max_completion_tokens are not both set
        if self.max_tokens is not None and self.max_completion_tokens is not None:
            raise ValueError(
                "Only one of max_tokens and max_completion_tokens can be set"
            )
        
        # Validate temperature and top_p
        if self.temperature == 0 and self.top_p is not None and self.top_p < 1:
            # This is a warning in OpenAI API, we allow it
            pass
            
        return self


class EmbeddingRequest(BaseModel):
    """Embedding request following OpenAI API format."""
    
    input: Union[str, list[str], list[int], list[list[int]]]
    model: str
    encoding_format: Literal["float", "base64"] = "float"
    dimensions: Optional[int] = Field(default=None, ge=1)
    user: Optional[str] = None


class ImageGenerationRequest(BaseModel):
    """Image generation request following OpenAI API format."""
    
    prompt: str = Field(..., min_length=1, max_length=4000)
    model: Optional[str] = "dall-e-2"
    n: Optional[int] = Field(default=1, ge=1, le=10)
    quality: Optional[Literal["standard", "hd"]] = "standard"
    response_format: Optional[Literal["url", "b64_json"]] = "url"
    size: Optional[Literal["256x256", "512x512", "1024x1024", "1792x1024", "1024x1792"]] = "1024x1024"
    style: Optional[Literal["vivid", "natural"]] = "vivid"
    user: Optional[str] = None


class AudioTranscriptionRequest(BaseModel):
    """Audio transcription request."""
    
    file: bytes
    model: str = "whisper-1"
    language: Optional[str] = None
    prompt: Optional[str] = None
    response_format: Optional[Literal["json", "text", "srt", "verbose_json", "vtt"]] = "json"
    temperature: Optional[float] = Field(default=0, ge=0, le=1)
    timestamp_granularities: Optional[list[Literal["word", "segment"]]] = None


class AudioSpeechRequest(BaseModel):
    """Text-to-speech request."""
    
    model: str = "tts-1"
    input: str = Field(..., min_length=1, max_length=4096)
    voice: Literal["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
    response_format: Optional[Literal["mp3", "opus", "aac", "flac", "wav", "pcm"]] = "mp3"
    speed: Optional[float] = Field(default=1.0, ge=0.25, le=4.0)
