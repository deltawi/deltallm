from __future__ import annotations

from typing import Any, Literal

from src.models.output_limits import ModelOutputTokenLimits, OutputTokenLimit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
)

from .requests import AssistantChatMessage


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    # Provider breakdowns (cached_tokens, reasoning_tokens, audio_tokens, ...) returned to
    # the client unchanged. Chat charges read only the counters above and
    # prompt_tokens_cached, never these objects.
    prompt_tokens_details: dict[str, JsonValue] | None = None
    completion_tokens_details: dict[str, JsonValue] | None = None

    @field_validator("prompt_tokens_details", "completion_tokens_details", mode="before")
    @classmethod
    def ignore_malformed_details(cls, value: object) -> object:
        # A provider's breakdown is informational; a malformed one must not fail the answer.
        return value if isinstance(value, dict) else None

    @model_serializer(mode="wrap")
    def serialize_details(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        data = handler(self)
        for field in ("prompt_tokens_details", "completion_tokens_details"):
            if data.get(field) is None:
                data.pop(field, None)
        return data


class ResponseAssistantMessage(AssistantChatMessage):
    # Answer-only fields such as refusal and annotations reach the client.
    model_config = ConfigDict(extra="allow")


class Choice(BaseModel):
    index: int
    message: ResponseAssistantMessage
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter"] | None = None
    logprobs: dict[str, Any] | None = None


class ChatCompletionResponse(BaseModel):
    # Top-level provider fields such as service_tier reach the client.
    model_config = ConfigDict(extra="allow")

    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage
    system_fingerprint: str | None = None


class CompletionChoice(BaseModel):
    text: str
    index: int
    logprobs: dict[str, Any] | None = None
    finish_reason: str | None = None


class CompletionsResponse(BaseModel):
    id: str
    object: Literal["text_completion"] = "text_completion"
    created: int
    model: str
    choices: list[CompletionChoice]
    usage: Usage
    system_fingerprint: str | None = None


class ResponsesOutputText(BaseModel):
    type: Literal["output_text"] = "output_text"
    text: str


class ResponsesOutputMessage(BaseModel):
    id: str
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    content: list[ResponsesOutputText]


class ResponsesResponse(BaseModel):
    id: str
    object: Literal["response"] = "response"
    created_at: int
    model: str
    output: list[ResponsesOutputMessage]
    usage: Usage
    status: Literal["completed"] = "completed"


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
    owner_account_id: str | None = None
    models: list[str] = Field(default_factory=list)
    team_models: list[str] = Field(default_factory=list)
    max_budget: float | None = None
    spend: float = 0.0
    # Legacy key-level limit aliases preserved for compatibility.
    tpm_limit: int | None = None
    rpm_limit: int | None = None
    key_tpm_limit: int | None = None
    key_output_tpm_limit: OutputTokenLimit | None = None
    key_model_output_tpm_limit: ModelOutputTokenLimits | None = None
    key_rpm_limit: int | None = None
    user_tpm_limit: int | None = None
    user_output_tpm_limit: OutputTokenLimit | None = None
    user_rpm_limit: int | None = None
    team_tpm_limit: int | None = None
    team_output_tpm_limit: OutputTokenLimit | None = None
    team_model_output_tpm_limit: ModelOutputTokenLimits | None = None
    team_rpm_limit: int | None = None
    org_tpm_limit: int | None = None
    org_output_tpm_limit: OutputTokenLimit | None = None
    org_rpm_limit: int | None = None
    team_model_rpm_limit: dict[str, int] | None = None
    team_model_tpm_limit: dict[str, int] | None = None
    org_model_rpm_limit: dict[str, int] | None = None
    org_model_tpm_limit: dict[str, int] | None = None
    max_parallel_requests: int | None = None
    key_rph_limit: int | None = None
    key_rpd_limit: int | None = None
    key_tpd_limit: int | None = None
    user_rph_limit: int | None = None
    user_rpd_limit: int | None = None
    user_tpd_limit: int | None = None
    team_rph_limit: int | None = None
    team_rpd_limit: int | None = None
    team_tpd_limit: int | None = None
    org_rph_limit: int | None = None
    org_rpd_limit: int | None = None
    org_tpd_limit: int | None = None
    guardrails: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] | None = None
    team_metadata: dict[str, Any] | None = None
    org_metadata: dict[str, Any] | None = None
    expires: str | None = None


class ErrorResponse(BaseModel):
    error: dict[str, Any]
