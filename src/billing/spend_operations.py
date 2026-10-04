"""Typed ordinary-operation journal contracts; no monetary reservation claim."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from src.models.errors import RoutingFailureAction, ServiceUnavailableError


class SpendPersistenceUnavailable(ServiceUnavailableError):
    error_type = "spend_persistence_unavailable"
    message = "Required usage persistence is temporarily unavailable"

    def __init__(self) -> None:
        super().__init__(
            code=self.error_type,
            affects_deployment_health=False,
            routing_failure_action=RoutingFailureAction.FAIL_FAST,
        )


class OperationPrincipal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    api_key: str = Field(min_length=1, max_length=512, repr=False)
    user_id: str | None = Field(default=None, max_length=512, repr=False)
    team_id: str | None = Field(default=None, max_length=512, repr=False)
    organization_id: str | None = Field(default=None, max_length=512, repr=False)
    owner_account_id: str | None = Field(default=None, max_length=512, repr=False)


class OperationAttempt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    deployment_id: str = Field(min_length=1, max_length=512)
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=512)
    pricing: dict[str, str | int | bool | None] = Field(max_length=64)
    outcome: Literal["unknown"] = "unknown"


class SpendOperationIntent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1] = 1
    principal: OperationPrincipal = Field(repr=False)
    model: str = Field(min_length=1, max_length=512)
    call_type: Literal[
        "completion",
        "embedding",
        "image_generation",
        "audio_speech",
        "audio_transcription",
        "rerank",
    ]
    started_at: AwareDatetime
    attempts: tuple[OperationAttempt, ...] = Field(min_length=1, max_length=128)


class OperationHandle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    event_id: UUID
    owner_token: UUID = Field(repr=False)
    intent: SpendOperationIntent = Field(repr=False)
    expires_at: AwareDatetime
