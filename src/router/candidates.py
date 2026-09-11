from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Protocol, Sequence

from src.router.health_state import DeploymentHealthRef

if TYPE_CHECKING:
    from src.router.router import Deployment


ROUTING_MODE_CONTEXT_KEY = "routing_mode"
_CANDIDATE_PLANS_CONTEXT_KEY = "_deltallm_candidate_plans"
DEFAULT_ATTEMPT_PERMIT_TTL_SECONDS = 630

UsageCounterName = Literal[
    "rpm",
    "tpm",
    "image_pm",
    "audio_seconds_pm",
    "char_pm",
    "rerank_units_pm",
]


class AttemptRejectionReason(str, Enum):
    STATIC_POLICY = "static_policy"
    COOLDOWN = "cooldown"
    UNHEALTHY = "unhealthy"
    RECOVERY_IN_PROGRESS = "recovery_in_progress"
    CAPACITY = "capacity"


@dataclass(frozen=True, slots=True)
class AttemptCapacityLimit:
    counter: UsageCounterName
    limit: int
    consume: int = 0

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("attempt capacity limit must be positive")
        if type(self.consume) is not int or self.consume < 0:
            raise ValueError("attempt consumption must be a nonnegative integer")


@dataclass(frozen=True, slots=True)
class AttemptCapacity:
    limits: tuple[AttemptCapacityLimit, ...] = ()
    max_concurrency: int | None = None
    require_shared: bool = False
    owner_token: str | None = None

    def __post_init__(self) -> None:
        if len({item.counter for item in self.limits}) != len(self.limits):
            raise ValueError("attempt counters must be unique")
        if self.max_concurrency is not None and (
            type(self.max_concurrency) is not int or not 1 <= self.max_concurrency <= 2**31 - 1
        ):
            raise ValueError("attempt concurrency must be positive")
        if self.require_shared and any(
            type(item.limit) is not int
            or not 1 <= item.limit <= 2**31 - 1
            or item.consume > 2**31 - 1
            for item in self.limits
        ):
            raise ValueError("shared attempt limits must be bounded integers")
        if self.owner_token is not None and not 16 <= len(self.owner_token) <= 128:
            raise ValueError("attempt owner token must be bounded")
        if (
            self.max_concurrency or any(item.consume for item in self.limits)
        ) and not self.require_shared:
            raise ValueError("reserved provider capacity requires shared coordination")


@dataclass(frozen=True, slots=True)
class AttemptPermit:
    deployment_id: str
    health_ref: DeploymentHealthRef
    acquired: bool
    backend: Literal["redis", "local"] | None = None
    owner_token: str | None = None
    expires_at_ms: int | None = None
    active_requests: int | None = None
    recovery: bool = False
    rejection_reason: AttemptRejectionReason | None = None


@dataclass(frozen=True, slots=True)
class RouteCandidateLane:
    """A policy lane after hard eligibility; lower lanes are empty after selection."""

    lane: str
    rank: int
    deployments: tuple[Deployment, ...]


@dataclass(frozen=True, slots=True)
class RouteCandidatePlan:
    """Request-scoped, policy-ordered deployments eligible for failover attempts."""

    model_group: str
    strategy: str
    deployments: tuple[Deployment, ...]
    candidate_count: int
    healthy_count: int
    filtered_count: int
    rejection_reason: str | None = None
    context_eligible_count: int | None = None
    lanes: tuple[RouteCandidateLane, ...] = ()
    minimum_rank: int | None = None


class RouteCandidatePlanner(Protocol):
    async def plan_deployments(
        self,
        model_groups: Sequence[str],
        request_context: dict[str, Any],
    ) -> dict[str, RouteCandidatePlan]: ...

    async def acquire_attempt(
        self,
        deployment: Deployment,
        request_context: dict[str, Any],
        *,
        lease_ttl_seconds: int = DEFAULT_ATTEMPT_PERMIT_TTL_SECONDS,
    ) -> AttemptPermit: ...

    async def release_attempt(self, permit: AttemptPermit) -> int | None: ...


def candidate_plan_cache(
    request_context: dict[str, Any],
) -> dict[str, RouteCandidatePlan]:
    cached = request_context.get(_CANDIDATE_PLANS_CONTEXT_KEY)
    if isinstance(cached, dict) and all(
        isinstance(group, str) and isinstance(plan, RouteCandidatePlan)
        for group, plan in cached.items()
    ):
        return cached

    plans: dict[str, RouteCandidatePlan] = {}
    request_context[_CANDIDATE_PLANS_CONTEXT_KEY] = plans
    return plans


def invalidate_candidate_plan_cache(request_context: dict[str, Any]) -> None:
    request_context.pop(_CANDIDATE_PLANS_CONTEXT_KEY, None)
