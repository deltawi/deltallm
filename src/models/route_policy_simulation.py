"""Share routing simulation contracts without importing the API route graph."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


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
