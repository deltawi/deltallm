from __future__ import annotations

from datetime import datetime
from copy import deepcopy
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ModelWrapValidatorHandler,
    PrivateAttr,
    StrictBool,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from src.route_policy_contract import LLMTierSelectorPolicy, RoutePolicyMember


class RouteGroupResponse(BaseModel):
    route_group_id: str
    group_key: str
    name: str | None
    mode: str
    routing_strategy: str | None
    enabled: bool
    member_count: int
    metadata: dict[str, Any] | None
    default_prompt: dict[str, str] | None = None
    owner_scope_type: str = "global"
    owner_scope_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RouteGroupMutationResponse(RouteGroupResponse):
    warnings: list[str] = Field(default_factory=list)


class RouteGroupMemberMutationResponse(BaseModel):
    membership_id: str
    route_group_id: str
    deployment_id: str
    enabled: bool
    weight: int | None
    priority: int | None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)


class RouteGroupDeleteResponse(BaseModel):
    deleted: bool
    warnings: list[str] = Field(default_factory=list)


class RoutePolicyResponse(BaseModel):
    route_policy_id: str
    route_group_id: str
    version: int
    semantics_version: int
    status: str
    policy_json: dict[str, Any]
    published_at: datetime | None
    published_by: str | None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RoutePolicyMutationResponse(BaseModel):
    group_key: str
    policy: RoutePolicyResponse
    warnings: list[str] = Field(default_factory=list)


class RoutePolicyRollbackResponse(RoutePolicyMutationResponse):
    rolled_back_from_version: int


class RoutePolicyContextDocument(BaseModel):
    """Typed context-routing fields with forward-compatible opaque extensions."""

    model_config = ConfigDict(extra="allow")

    mode: Literal["eligible-only", "smallest-sufficient"] = "eligible-only"
    unknown_capacity: Literal["allow", "exclude"] = "allow"
    default_output_tokens: int = Field(default=1024, ge=0)
    safety_margin_tokens: int = Field(default=256, ge=0)

    @field_validator("default_output_tokens", "safety_margin_tokens", mode="before")
    @classmethod
    def reject_boolean_token_settings(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("context token settings must be non-negative integers")
        return value


class RoutePolicyDocumentRequest(BaseModel):
    """Typed latest policy shape with opaque compatibility for selector-free documents."""

    model_config = ConfigDict(extra="allow")

    mode: object | None = None
    strategy: object | None = None
    members: list[RoutePolicyMember | dict[str, object]] = Field(default_factory=list)
    timeouts: dict[str, object] | object | None = None
    retry: dict[str, object] | object | None = None
    context: RoutePolicyContextDocument | dict[str, object] | None = None
    selector: LLMTierSelectorPolicy | None = None
    _document: dict[str, Any] = PrivateAttr(default_factory=dict)

    @model_validator(mode="wrap")
    @classmethod
    def preserve_authored_document(
        cls, value: Any, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        result = handler(value)
        # Inherited-selector strictness is decided under the repository's group lock.
        # Transport normalization must not erase values, unknown keys, or tombstones.
        if isinstance(value, dict):
            result._document = deepcopy(value)
        return result

    def to_policy_document(self) -> dict[str, Any]:
        return deepcopy(self._document)


class RoutePolicyTimeoutsDocument(BaseModel):
    model_config = ConfigDict(extra="allow")

    global_ms: int | None = Field(default=None, ge=1)
    global_seconds: float | None = Field(default=None, gt=0)


class RoutePolicyRetryDocument(BaseModel):
    model_config = ConfigDict(extra="allow")

    max_attempts: int | None = Field(default=None, ge=0)
    retryable_error_classes: list[str] | None = None


class RoutePolicyDocumentResponse(BaseModel):
    """Normalized latest policy projection; unknown future fields remain readable."""

    model_config = ConfigDict(extra="allow")

    mode: str | None = None
    strategy: str | None = None
    members: list[RoutePolicyMember] | None = None
    timeouts: RoutePolicyTimeoutsDocument | None = None
    retry: RoutePolicyRetryDocument | None = None
    context: RoutePolicyContextDocument | None = None
    selector: LLMTierSelectorPolicy | None = None


class RoutePolicyCurrentResponse(BaseModel):
    group_key: str
    policy: RoutePolicyResponse | None


class RoutePolicyHistoryResponse(BaseModel):
    group_key: str
    policies: list[RoutePolicyResponse]


class RoutePolicyValidationResponse(BaseModel):
    group_key: str
    valid: Literal[True] = True
    policy: RoutePolicyDocumentResponse
    warnings: list[str] = Field(default_factory=list)


RoutePolicySimulationOutcome = Literal[
    "success",
    "timeout",
    "rate_limit",
    "unavailable",
]


class RoutePolicySimulationDeploymentOutcome(BaseModel):
    deployment_id: str = Field(min_length=1, max_length=256)
    outcome: RoutePolicySimulationOutcome

    @field_validator("deployment_id")
    @classmethod
    def normalize_deployment_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("deployment_id must not be blank")
        return normalized


class RoutePolicySimulationRequest(BaseModel):
    iterations: int = Field(default=100, ge=1, le=5000)
    input_tokens: int = Field(default=0, ge=0)
    requested_output_tokens: int | None = Field(default=None, ge=0)
    policy: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    user_id: str = Field(default="policy-simulation", min_length=1, max_length=256)
    prompt_ref: dict[str, Any] | None = None
    outcomes: list[RoutePolicySimulationDeploymentOutcome] = Field(
        default_factory=list,
        max_length=500,
    )


class RoutePolicySimulationPrompt(BaseModel):
    template_key: str
    version: int
    label: str | None = None
    route_preferences: dict[str, Any]


class RoutePolicySimulationSelection(BaseModel):
    deployment_id: str
    count: int
    ratio: float


class RoutePolicySimulationSummary(BaseModel):
    selected_requests: int
    no_selection_requests: int
    served_requests: int
    failed_requests: int
    fallback_requests: int
    timed_out_requests: int
    total_attempts: int


class RoutePolicySimulationAttempt(BaseModel):
    iteration: int
    attempt: int
    deployment_id: str
    outcome: RoutePolicySimulationOutcome
    transition: Literal["primary", "retry", "fallback"]


class RoutePolicySimulationResponse(BaseModel):
    group_key: str
    iterations: int
    basis: Literal["live_state_dry_run"] = "live_state_dry_run"
    warnings: list[str] = Field(default_factory=list)
    prompt: RoutePolicySimulationPrompt | None = None
    effective_metadata: dict[str, Any]
    summary: RoutePolicySimulationSummary
    reason_counts: dict[str, int]
    selections: list[RoutePolicySimulationSelection]
    served_deployments: list[RoutePolicySimulationSelection]
    terminal_outcomes: dict[str, int]
    sample_decision: dict[str, Any] | None = None
    sample_attempts: list[RoutePolicySimulationAttempt] = Field(default_factory=list)


class RouteGroupDetailMember(RouteGroupMemberMutationResponse):
    model_name: str | None = None
    provider: str | None = None
    mode: str | None = None
    healthy: bool | None = None


class RouteGroupBindingResponse(BaseModel):
    route_group_binding_id: str
    route_group_id: str
    group_key: str
    scope_type: str
    scope_id: str
    enabled: bool
    metadata: dict[str, JsonValue] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RouteGroupDetailResponse(BaseModel):
    group: RouteGroupResponse
    members: list[RouteGroupDetailMember]
    policy: RoutePolicyResponse | None
    bindings: list[RouteGroupBindingResponse]


class RouteGroupResolutionResponse(BaseModel):
    route_group_id: str
    group_key: str


class RouteGroupUpdateRequest(BaseModel):
    name: str | None = None
    mode: str | None = None
    strategy: str | None = None
    enabled: StrictBool = True
    metadata: dict[str, JsonValue] | None = None
    default_prompt: dict[str, str | None] | None = None
    owner_scope_type: str | None = None
    owner_scope_id: str | None = None


class RouteGroupMemberWriteRequest(BaseModel):
    deployment_id: str = Field(min_length=1)
    enabled: StrictBool = True
    weight: int | None = None
    priority: int | None = None

    @field_validator("weight", "priority", mode="before")
    @classmethod
    def reject_boolean_numbers(cls, value: object) -> object:
        # Reject before Pydantic turns True/False into 1/0. Retain numeric-string
        # compatibility with the existing member endpoint.
        if isinstance(value, bool):
            raise PydanticCustomError("int_type", "Input should be a valid integer")
        return value


class RoutePolicyRollbackRequest(BaseModel):
    version: int = Field(ge=1, strict=True)


class RouteGroupErrorResponse(BaseModel):
    detail: str
