from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.services.admission.rate_limit_contracts import (
    LegacyParallelLease,
    ParallelLimitCheck,
    ParallelLimitLease,
)

if TYPE_CHECKING:
    from src.services.admission.output_token_context import OutputTokenContext


@dataclass
class RateLimitState:
    rpm_limit: int = 0
    rpm_remaining: int = 0
    rpm_reset: int = 0
    rpm_scope: str = ""
    tpm_limit: int = 0
    tpm_remaining: int = 0
    tpm_reset: int = 0
    tpm_scope: str = ""
    output_tpm_limit: int = 0
    output_tpm_remaining: int | None = 0
    output_tpm_reset: int = 0
    output_tpm_scope: str = ""
    warning: str | None = None


@dataclass(slots=True)
class RateLimitLease:
    legacy_parallel_lease: LegacyParallelLease | None = None
    parallel_leases: tuple[ParallelLimitLease, ...] = ()
    output_context: OutputTokenContext | None = None
    _pending_parallel_leases: list[ParallelLimitLease] = field(init=False, repr=False)
    _legacy_parallel_pending: bool = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._pending_parallel_leases = list(self.parallel_leases)
        self._legacy_parallel_pending = self.legacy_parallel_lease is not None

    @property
    def pending_parallel_acquisitions(self) -> tuple[ParallelLimitCheck, ...]:
        checks = []
        if self._legacy_parallel_pending and self.legacy_parallel_lease is not None:
            checks.append(self.legacy_parallel_lease.check)
        checks.extend(lease.check for lease in self._pending_parallel_leases)
        return tuple(checks)

    @property
    def pending_parallel_leases(self) -> tuple[ParallelLimitLease, ...]:
        return tuple(self._pending_parallel_leases)

    @property
    def pending_legacy_parallel_lease(self) -> LegacyParallelLease | None:
        if not self._legacy_parallel_pending:
            return None
        return self.legacy_parallel_lease

    @property
    def refreshable_parallel_leases(self) -> tuple[ParallelLimitLease, ...]:
        return tuple(lease for lease in self._pending_parallel_leases if lease.backend == "redis")

    @property
    def refreshable_legacy_parallel_lease(self) -> LegacyParallelLease | None:
        if not self._legacy_parallel_pending:
            return None
        if self.legacy_parallel_lease is None or self.legacy_parallel_lease.backend != "redis":
            return None
        return self.legacy_parallel_lease

    def mark_parallel_released(self, lease: ParallelLimitLease) -> None:
        with suppress(ValueError):
            self._pending_parallel_leases.remove(lease)

    def mark_legacy_parallel_released(self) -> None:
        self._legacy_parallel_pending = False
