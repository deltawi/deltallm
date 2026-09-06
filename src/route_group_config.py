from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.governance.access_groups import normalize_access_group_list
from src.route_policy_contract import (
    LLMTierSelectorPolicy,
    RoutePolicyMember,
    validate_selector_assignments,
)

ModelMode = Literal[
    "chat",
    "embedding",
    "image_generation",
    "audio_speech",
    "audio_transcription",
    "rerank",
]
SUPPORTED_MODEL_MODES = frozenset(
    {
        "chat",
        "embedding",
        "image_generation",
        "audio_speech",
        "audio_transcription",
        "rerank",
    }
)
CONTEXT_ROUTING_MODEL_MODES = frozenset({"chat", "embedding"})


def validate_context_routing_workload_mode(workload_mode: object) -> None:
    normalized = str(workload_mode or "").strip().lower()
    if normalized in CONTEXT_ROUTING_MODEL_MODES:
        return
    supported = ", ".join(sorted(CONTEXT_ROUTING_MODEL_MODES))
    actual = normalized or "unknown"
    raise ValueError(
        f"context routing is not supported for route group mode '{actual}'; "
        f"supported modes: {supported}"
    )

RoutingStrategyName = Literal[
    "simple-shuffle",
    "least-busy",
    "latency-based-routing",
    "cost-based-routing",
    "usage-based-routing",
    "tag-based-routing",
    "priority-based-routing",
    "weighted",
    "rate-limit-aware",
]


class RouteGroupMember(BaseModel):
    """File-config member shape; legacy selector-free coercion remains compatible."""

    deployment_id: str
    enabled: bool = True
    weight: int | None = None
    priority: int | None = None
    lane: str | None = None

    @field_validator("lane", mode="before")
    @classmethod
    def normalize_lane(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class ContextRoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


class RouteGroupConfig(BaseModel):
    key: str
    # Omitted mode is retained for compatibility with pre-mode file configs and
    # resolved from enabled deployments during complete runtime construction.
    mode: ModelMode | None = None
    enabled: bool = True
    strategy: RoutingStrategyName | None = None
    access_groups: list[str] = Field(default_factory=list)
    members: list[RouteGroupMember] = Field(default_factory=list)
    context: ContextRoutingConfig | None = None
    selector: LLMTierSelectorPolicy | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_selector_owned_keys_and_members(cls, value: object) -> object:
        if not isinstance(value, dict) or value.get("selector") is None:
            return value

        allowed_group_keys = {
            "key",
            "mode",
            "enabled",
            "strategy",
            "access_groups",
            "members",
            "context",
            "selector",
        }
        unknown_group_keys = sorted(set(value) - allowed_group_keys)
        if unknown_group_keys:
            raise ValueError(
                "selector route group contains unknown fields: " + ", ".join(unknown_group_keys)
            )

        allowed_member_keys = {"deployment_id", "enabled", "weight", "priority", "lane"}
        raw_members = value.get("members")
        if not isinstance(raw_members, list):
            return value

        strict_members: list[dict[str, object]] = []
        for index, raw_member in enumerate(raw_members):
            if isinstance(raw_member, dict):
                unknown_member_keys = sorted(set(raw_member) - allowed_member_keys)
                if unknown_member_keys:
                    raise ValueError(
                        f"selector route-group member {index} contains unknown fields: "
                        + ", ".join(unknown_member_keys)
                    )
            strict_member = RoutePolicyMember.model_validate(raw_member)
            strict_members.append(strict_member.model_dump(mode="python", exclude_none=True))

        normalized = dict(value)
        normalized["members"] = strict_members
        return normalized

    @field_validator("access_groups", mode="before")
    @classmethod
    def validate_access_groups(cls, value: object) -> list[str]:
        return normalize_access_group_list(value, strict=True)

    @model_validator(mode="after")
    def validate_routing_policies(self) -> Self:
        if self.context is not None and self.mode is not None:
            validate_context_routing_workload_mode(self.mode)
        if self.selector is None:
            if any(member.lane is not None for member in self.members):
                raise ValueError("route-group member lanes require a selector")
            return self

        validate_selector_assignments(
            self.selector,
            [RoutePolicyMember.model_validate(member.model_dump()) for member in self.members],
            group_mode=self.mode,
        )
        return self


class RouterSettings(BaseModel):
    routing_strategy: RoutingStrategyName = "simple-shuffle"
    num_retries: int = 0
    retry_after: float = 0
    timeout: float = 600
    cooldown_time: int = 60
    allowed_fails: int = 2
    enable_pre_call_checks: bool = False
    model_group_alias: dict[str, str] = Field(default_factory=dict)
    route_groups: list[RouteGroupConfig] = Field(default_factory=list)
