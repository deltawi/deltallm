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

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("attempt capacity limit must be positive")


@dataclass(frozen=True, slots=True)
class AttemptCapacity:
    limits: tuple[AttemptCapacityLimit, ...] = ()


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
class RouteCandidatePlan:
    """Request-scoped, policy-ordered deployments eligible for failover attempts."""

    model_group: str
    strategy: str
    deployments: tuple[Deployment, ...]
    candidate_count: int
    healthy_count: int
    filtered_count: int


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
