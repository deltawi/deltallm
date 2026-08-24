from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class RouteGroupMutationResponse(BaseModel):
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
