from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .requests import ChatMessage


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class Choice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter"] | None = None
    logprobs: dict[str, Any] | None = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage
    system_fingerprint: str | None = None


class EmbeddingData(BaseModel):
    object: Literal["embedding"] = "embedding"
    embedding: list[float]
    index: int


class EmbeddingResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[EmbeddingData]
    model: str
    usage: Usage


class ModelInfo(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str


class ModelsResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelInfo]


class UserAPIKeyAuth(BaseModel):
    api_key: str
    user_id: str | None = None
    team_id: str | None = None
    organization_id: str | None = None
    user_role: str | None = None
    models: list[str] = Field(default_factory=list)
    max_budget: float | None = None
    spend: float = 0.0
    # Legacy key-level limit aliases preserved for compatibility.
    tpm_limit: int | None = None
    rpm_limit: int | None = None
    key_tpm_limit: int | None = None
    key_rpm_limit: int | None = None
    user_tpm_limit: int | None = None
    user_rpm_limit: int | None = None
    team_tpm_limit: int | None = None
    team_rpm_limit: int | None = None
    org_tpm_limit: int | None = None
    org_rpm_limit: int | None = None
    max_parallel_requests: int | None = None
    guardrails: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] | None = None
    expires: str | None = None


class ErrorResponse(BaseModel):
    error: dict[str, Any]
