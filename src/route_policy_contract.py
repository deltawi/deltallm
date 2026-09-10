from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTEXT_POLICY_SEMANTICS_VERSION = 2
SELECTOR_POLICY_SEMANTICS_VERSION = 3
MIN_SELECTOR_TIMEOUT_MS = 100
MAX_SELECTOR_TIMEOUT_MS = 5_000
MIN_SELECTOR_INPUT_CHARS = 256
MAX_SELECTOR_INPUT_CHARS = 32_768
MIN_SELECTOR_LANES = 2
MAX_SELECTOR_LANES = 8
MAX_DEPLOYMENT_ID_LENGTH = 256
MAX_LANE_ID_LENGTH = 32
MAX_LANE_DESCRIPTION_LENGTH = 512
LANE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class SelectorLane(BaseModel):
    """One bounded capability/cost lane in ascending capability order."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(
        min_length=1,
        max_length=MAX_LANE_ID_LENGTH,
        pattern=LANE_ID_PATTERN.pattern,
    )
    rank: int = Field(ge=0, lt=MAX_SELECTOR_LANES)
    description: str = Field(min_length=1, max_length=MAX_LANE_DESCRIPTION_LENGTH)

    @field_validator("id", "description", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not LANE_ID_PATTERN.fullmatch(value):
            raise ValueError(
                "must start with a lowercase letter and contain only lowercase letters, "
                "digits, '_' or '-'"
            )
        return value


class LLMTierSelectorPolicy(BaseModel):
    """Durable v1 contract for an LLM-backed lane classifier."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["llm-tier"]
    classifier_deployment_id: str = Field(
        min_length=1,
        max_length=MAX_DEPLOYMENT_ID_LENGTH,
    )
    timeout_ms: int = Field(
        default=750,
        ge=MIN_SELECTOR_TIMEOUT_MS,
        le=MAX_SELECTOR_TIMEOUT_MS,
    )
    max_input_chars: int = Field(
        default=8_000,
        ge=MIN_SELECTOR_INPUT_CHARS,
        le=MAX_SELECTOR_INPUT_CHARS,
    )
    default_lane: str | None = Field(
        default=None,
        max_length=MAX_LANE_ID_LENGTH,
        pattern=LANE_ID_PATTERN.pattern,
    )
    lanes: tuple[SelectorLane, ...] = Field(
        min_length=MIN_SELECTOR_LANES,
        max_length=MAX_SELECTOR_LANES,
    )

    @field_validator("classifier_deployment_id", "default_lane", mode="before")
    @classmethod
    def strip_identifier(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("lanes", mode="before")
    @classmethod
    def normalize_lanes_container(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def validate_lanes(self) -> Self:
        lane_ids = [lane.id for lane in self.lanes]
        if len(set(lane_ids)) != len(lane_ids):
            raise ValueError("lane ids must be unique")

        ranks = [lane.rank for lane in self.lanes]
        if len(set(ranks)) != len(ranks):
            raise ValueError("lane ranks must be unique")
        if sorted(ranks) != list(range(len(self.lanes))):
            raise ValueError("lane ranks must be contiguous and start at 0")

        ordered_lanes = tuple(sorted(self.lanes, key=lambda lane: lane.rank))
        default_lane = self.default_lane or ordered_lanes[-1].id
        if default_lane not in lane_ids:
            raise ValueError("default_lane must name a configured lane")

        object.__setattr__(self, "lanes", ordered_lanes)
        object.__setattr__(self, "default_lane", default_lane)
        return self


class RoutePolicyMember(BaseModel):
    """Strict member contract used when a selector is present."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    deployment_id: str = Field(min_length=1, max_length=MAX_DEPLOYMENT_ID_LENGTH)
    enabled: bool = True
    weight: int | None = Field(default=None, ge=1)
    priority: int | None = Field(default=None, ge=0)
    lane: str | None = Field(
        default=None,
        max_length=MAX_LANE_ID_LENGTH,
        pattern=LANE_ID_PATTERN.pattern,
    )

    @field_validator("deployment_id", "lane", mode="before")
    @classmethod
    def strip_identifier(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized

    @field_validator("lane")
    @classmethod
    def validate_lane(cls, value: str | None) -> str | None:
        if value is not None and not LANE_ID_PATTERN.fullmatch(value):
            raise ValueError("must be a valid lane id")
        return value


class SelectorDeploymentInventory(Protocol):
    workload_mode: str | None


class SelectorMemberInventory(SelectorDeploymentInventory, Protocol):
    enabled: bool


def validate_selector_reference(
    selector: LLMTierSelectorPolicy,
    deployments: Mapping[str, SelectorDeploymentInventory],
) -> None:
    """Resolve an internal classifier dependency, independently of answer membership."""
    target = deployments.get(selector.classifier_deployment_id)
    if target is None:
        raise ValueError("selector classifier must reference an existing concrete deployment")
    if target.workload_mode != "chat":
        raise ValueError("selector classifier must reference a chat deployment")


def validate_selector_assignments(
    selector: LLMTierSelectorPolicy,
    members: Sequence[RoutePolicyMember],
    *,
    group_mode: str | None,
    available_members: Mapping[str, SelectorMemberInventory] | None = None,
) -> None:
    """Validate only the effective answer-member lane partition."""

    if group_mode != "chat":
        raise ValueError("selector requires route group mode 'chat'")

    member_ids = [member.deployment_id for member in members]
    if len(set(member_ids)) != len(member_ids):
        raise ValueError("selector member deployment ids must be unique")
    lane_ids = {lane.id for lane in selector.lanes}
    active_lane_ids: set[str] = set()
    for member in members:
        inventory_member = (
            available_members.get(member.deployment_id) if available_members is not None else None
        )
        if available_members is not None and inventory_member is None:
            if not member.enabled:
                continue
            raise ValueError(
                f"selector member '{member.deployment_id}' must be a route-group member"
            )
        effective_enabled = member.enabled and (
            inventory_member.enabled if inventory_member is not None else True
        )
        if not effective_enabled:
            continue
        if available_members is not None and inventory_member is not None:
            if inventory_member.workload_mode is None:
                raise ValueError(
                    f"selector member '{member.deployment_id}' must reference a concrete deployment"
                )
            if inventory_member.workload_mode != group_mode:
                raise ValueError(
                    f"selector member '{member.deployment_id}' must reference a {group_mode} "
                    "deployment"
                )
        if member.lane is None:
            raise ValueError(
                f"enabled selector member '{member.deployment_id}' must have exactly one lane"
            )
        if member.lane not in lane_ids:
            raise ValueError(
                f"member '{member.deployment_id}' references unknown lane '{member.lane}'"
            )
        active_lane_ids.add(member.lane)

    empty_lanes = sorted(lane_ids - active_lane_ids)
    if empty_lanes:
        raise ValueError(f"selector lanes have no enabled members: {', '.join(empty_lanes)}")
