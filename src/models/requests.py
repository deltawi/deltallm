from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]]
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ToolDefinition(BaseModel):
    type: Literal["function"] = "function"
    function: dict[str, Any]


class ToolChoice(BaseModel):
    type: Literal["function"] = "function"
    function: dict[str, str]


class ResponseFormat(BaseModel):
    type: Literal["text", "json_object", "json_schema"]
    json_schema: dict[str, Any] | None = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float | None = Field(default=1.0, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1)
    top_p: float | None = Field(default=1.0, ge=0, le=1)
    n: int | None = Field(default=1, ge=1, le=10)
    stream: bool | None = False
    stop: str | list[str] | None = None
    presence_penalty: float | None = Field(default=0, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=0, ge=-2, le=2)
    tools: list[ToolDefinition] | None = None
    tool_choice: Literal["auto", "none", "required"] | ToolChoice | None = "auto"
    response_format: ResponseFormat | None = None
    user: str | None = None
    metadata: dict[str, Any] | None = None


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str] | list[int] | list[list[int]]
    encoding_format: Literal["float", "base64"] | None = "float"
    dimensions: int | None = Field(default=None, ge=1)
    user: str | None = None
