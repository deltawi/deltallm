from typing import Literal

from pydantic import Field

from src.router.selection.contracts import (
    FrozenContract,
    SelectorCause,
    SelectorDecision,
    SelectorPolicyIdentity,
)


class ProtectedSelectorDecision(FrozenContract):
    kind: Literal["llm-tier"] = "llm-tier"
    lane: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    minimum_rank: int = Field(ge=0, lt=8)
    cause: SelectorCause
    latency_ms: float = Field(ge=0, allow_inf_nan=False)
    used_default: bool
    policy_identity: SelectorPolicyIdentity

    @classmethod
    def from_decision(cls, decision: SelectorDecision) -> "ProtectedSelectorDecision":
        return cls(
            lane=decision.lane,
            minimum_rank=decision.minimum_rank,
            cause=decision.cause,
            latency_ms=decision.latency_ms,
            used_default=decision.used_default,
            policy_identity=decision.policy_identity,
        )
