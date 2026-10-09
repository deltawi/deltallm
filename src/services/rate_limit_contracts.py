from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from src.services.output_limit_types import OutputSnapshot
from src.services.tier_capacity_fair_share import TierFairShareDecision


_PARALLEL_LEASE_TTL_SECONDS = 300


@dataclass(frozen=True)
class RateLimitCheck:
    scope: str
    entity_id: str
    limit: int
    amount: int = 1
    window_seconds: int = 60
    dimension: Literal["requests", "tokens", "output_tokens"] | None = None


@dataclass(frozen=True)
class ParallelLimitCheck:
    scope: str
    entity_id: str
    limit: int


@dataclass(frozen=True)
class LegacyParallelLease:
    scope: str
    entity_id: str
    limit: int
    backend: Literal["redis", "fallback"]
    ttl_seconds: int = _PARALLEL_LEASE_TTL_SECONDS

    @property
    def check(self) -> ParallelLimitCheck:
        return ParallelLimitCheck(scope=self.scope, entity_id=self.entity_id, limit=self.limit)


@dataclass(frozen=True)
class ParallelLimitLease:
    scope: str
    entity_id: str
    limit: int
    token: str
    backend: Literal["redis", "fallback"]
    ttl_seconds: int = _PARALLEL_LEASE_TTL_SECONDS

    @property
    def check(self) -> ParallelLimitCheck:
        return ParallelLimitCheck(scope=self.scope, entity_id=self.entity_id, limit=self.limit)


@dataclass(frozen=True)
class _ParallelLeaseGroup:
    check: ParallelLimitCheck
    requested_count: int


@dataclass
class RateLimitResult:
    checks: list[RateLimitCheck] = field(default_factory=list)
    current_values: list[int] = field(default_factory=list)
    window_reset_at: int = 0
    window_resets: list[int] = field(default_factory=list)
    output_snapshot: OutputSnapshot | None = None


@dataclass
class RateLimitAdmissionResult:
    rate_result: RateLimitResult = field(default_factory=RateLimitResult)
    fair_share_decisions: tuple[TierFairShareDecision, ...] = ()
    legacy_parallel_lease: LegacyParallelLease | None = None
    parallel_leases: tuple[ParallelLimitLease, ...] = ()
