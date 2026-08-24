from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.router.candidates import (
    DEFAULT_ATTEMPT_PERMIT_TTL_SECONDS,
    AttemptCapacity,
    AttemptPermit,
    AttemptRejectionReason,
)
from src.router.health_state import (
    DeploymentHealthState,
    HealthRefInput,
    HealthProbeClaim,
    HealthTransitionResult,
    coerce_health_ref,
)
from src.router.redis_keys import RouterHealthProbeScope
from src.router.state import DeploymentStateBackend


class RoutingStateSnapshotMiss(RuntimeError):
    """Raised when simulation would need mutable state after its snapshot is frozen."""


class RoutingSimulationState:
    """Read live routing state once, then provide isolated attempt semantics.

    Planning reads are cached by deployment and frozen before any simulated
    iteration. Attempt permits and health reporting are process-local no-ops so
    a control-plane dry run cannot influence live traffic.
    """

    def __init__(self, source: DeploymentStateBackend) -> None:
        self._source = source
        self._frozen = False
        self._health: dict[str, dict[str, Any]] = {}
        self._cooldowns: dict[str, bool] = {}
        self._active: dict[str, int] = {}
        self._usage: dict[str, dict[str, int]] = {}
        self._latency: dict[tuple[str, int], list[tuple[int, float]]] = {}
        self._owned_attempts: dict[str, int] = {}
        self._request_failures: dict[str, int] = {}
        self._request_cooldowns: set[str] = set()
        self._next_owner = 0

    def freeze(self) -> None:
        self._frozen = True

    def reset_attempt_effects(self) -> None:
        """Start an independent request against the same captured live state."""

        self._owned_attempts.clear()
        self._request_failures.clear()
        self._request_cooldowns.clear()

    def _require_loading(self, capability: str, deployment_ids: list[str]) -> None:
        if self._frozen:
            missing = ", ".join(sorted(deployment_ids))
            raise RoutingStateSnapshotMiss(
                f"routing simulation snapshot is missing {capability} state for: {missing}"
            )

    async def get_health_batch(
        self, health_refs: list[HealthRefInput]
    ) -> dict[str, dict[str, Any]]:
        resolved = [coerce_health_ref(item) for item in health_refs]
        missing = [item for item in resolved if item.deployment_id not in self._health]
        if missing:
            self._require_loading("health", [item.deployment_id for item in missing])
            loaded = await self._source.get_health_batch(missing)
            for item in missing:
                self._health[item.deployment_id] = dict(loaded.get(item.deployment_id, {}))
        return {item.deployment_id: dict(self._health[item.deployment_id]) for item in resolved}

    async def get_cooldown_batch(self, health_refs: list[HealthRefInput]) -> dict[str, bool]:
        resolved = [coerce_health_ref(item) for item in health_refs]
        missing = [item for item in resolved if item.deployment_id not in self._cooldowns]
        if missing:
            self._require_loading("cooldown", [item.deployment_id for item in missing])
            loaded = await self._source.get_cooldown_batch(missing)
            for item in missing:
                self._cooldowns[item.deployment_id] = bool(loaded.get(item.deployment_id, False))
        return {item.deployment_id: self._cooldowns[item.deployment_id] for item in resolved}

    async def get_active_requests_batch(self, deployment_ids: list[str]) -> dict[str, int]:
        missing = [item for item in deployment_ids if item not in self._active]
        if missing:
            self._require_loading("active-request", missing)
            loaded = await self._source.get_active_requests_batch(missing)
            for deployment_id in missing:
                self._active[deployment_id] = int(loaded.get(deployment_id, 0))
        return {deployment_id: self._active[deployment_id] for deployment_id in deployment_ids}

    async def get_usage_batch(self, deployment_ids: list[str]) -> dict[str, dict[str, int]]:
        missing = [item for item in deployment_ids if item not in self._usage]
        if missing:
            self._require_loading("usage", missing)
            loaded = await self._source.get_usage_batch(missing)
            for deployment_id in missing:
                self._usage[deployment_id] = {
                    str(key): int(value) for key, value in loaded.get(deployment_id, {}).items()
                }
        return {deployment_id: dict(self._usage[deployment_id]) for deployment_id in deployment_ids}

    async def get_latency_windows_batch(
        self,
        deployment_ids: list[str],
        window_ms: int,
    ) -> dict[str, list[tuple[int, float]]]:
        missing = [
            deployment_id
            for deployment_id in deployment_ids
            if (deployment_id, window_ms) not in self._latency
        ]
        if missing:
            self._require_loading("latency", missing)
            loaded = await self._source.get_latency_windows_batch(missing, window_ms)
            for deployment_id in missing:
                self._latency[(deployment_id, window_ms)] = list(loaded.get(deployment_id, []))
        return {
            deployment_id: list(self._latency[(deployment_id, window_ms)])
            for deployment_id in deployment_ids
        }

    async def acquire_attempt(
        self,
        health_ref: HealthRefInput,
        capacity: AttemptCapacity,
        *,
        lease_ttl_seconds: int = DEFAULT_ATTEMPT_PERMIT_TTL_SECONDS,
    ) -> AttemptPermit:
        del lease_ttl_seconds
        resolved = coerce_health_ref(health_ref)
        deployment_id = resolved.deployment_id
        if self._cooldowns.get(deployment_id, False) or deployment_id in self._request_cooldowns:
            return AttemptPermit(
                deployment_id=deployment_id,
                health_ref=resolved,
                acquired=False,
                rejection_reason=AttemptRejectionReason.COOLDOWN,
            )
        health = self._health.get(deployment_id, {})
        if health.get("healthy", "true") == "false" and health.get("recovery_required") != "true":
            return AttemptPermit(
                deployment_id=deployment_id,
                health_ref=resolved,
                acquired=False,
                rejection_reason=AttemptRejectionReason.UNHEALTHY,
            )
        usage = self._usage.get(deployment_id, {})
        if any(usage.get(item.counter, 0) >= item.limit for item in capacity.limits):
            return AttemptPermit(
                deployment_id=deployment_id,
                health_ref=resolved,
                acquired=False,
                rejection_reason=AttemptRejectionReason.CAPACITY,
            )

        self._next_owner += 1
        self._owned_attempts[deployment_id] = self._owned_attempts.get(deployment_id, 0) + 1
        return AttemptPermit(
            deployment_id=deployment_id,
            health_ref=resolved,
            acquired=True,
            backend="local",
            owner_token=f"simulation-{self._next_owner}",
            active_requests=self._active.get(deployment_id, 0)
            + self._owned_attempts[deployment_id],
        )

    async def release_attempt(self, permit: AttemptPermit) -> int | None:
        if not permit.acquired:
            return 0
        current = max(0, self._owned_attempts.get(permit.deployment_id, 0) - 1)
        if current:
            self._owned_attempts[permit.deployment_id] = current
        else:
            self._owned_attempts.pop(permit.deployment_id, None)
        return self._active.get(permit.deployment_id, 0) + current

    async def record_latency(self, deployment_id: str, latency_ms: float) -> None:
        del deployment_id, latency_ms

    async def increment_usage(
        self,
        deployment_id: str,
        tokens: int,
        window: str | None = None,
    ) -> None:
        del deployment_id, tokens, window

    async def increment_usage_counters(
        self,
        deployment_id: str,
        counters: Mapping[str, int],
        window: str | None = None,
    ) -> None:
        del deployment_id, counters, window

    async def apply_health_success(
        self,
        health_ref: HealthRefInput,
        *,
        recovery_token: str | None = None,
    ) -> HealthTransitionResult:
        del health_ref, recovery_token
        return HealthTransitionResult(applied=False, state=DeploymentHealthState.HEALTHY)

    async def apply_health_failure(
        self,
        health_ref: HealthRefInput,
        error: str,
        *,
        allowed_fails: int,
        cooldown_seconds: int,
        recovery_token: str | None = None,
    ) -> HealthTransitionResult:
        del error, cooldown_seconds, recovery_token
        resolved = coerce_health_ref(health_ref)
        deployment_id = resolved.deployment_id
        health = self._health.get(deployment_id, {})
        try:
            captured_failures = int(health.get("consecutive_failures", 0) or 0)
        except (TypeError, ValueError):
            captured_failures = 0
        failure_count = self._request_failures.get(deployment_id, captured_failures) + 1
        self._request_failures[deployment_id] = failure_count
        entered_cooldown = health.get("recovery_required") == "true" or failure_count > max(
            0, int(allowed_fails)
        )
        if entered_cooldown:
            self._request_cooldowns.add(deployment_id)
        return HealthTransitionResult(
            applied=True,
            state=(
                DeploymentHealthState.COOLDOWN
                if entered_cooldown
                else DeploymentHealthState.HEALTHY
            ),
            failure_count=failure_count,
            entered_cooldown=entered_cooldown,
        )

    async def apply_manual_cooldown(
        self,
        health_ref: HealthRefInput,
        duration_seconds: int,
        reason: str,
    ) -> None:
        del health_ref, duration_seconds, reason

    async def claim_health_probe(
        self,
        health_ref: HealthRefInput,
        ttl_seconds: int,
        *,
        scope: RouterHealthProbeScope = "background",
    ) -> HealthProbeClaim | None:
        del health_ref, ttl_seconds, scope
        return None

    async def release_health_probe(
        self,
        health_ref: HealthRefInput,
        claim: HealthProbeClaim,
    ) -> None:
        del health_ref, claim

    async def release_health_recovery(
        self,
        health_ref: HealthRefInput,
        owner_token: str,
    ) -> None:
        del health_ref, owner_token

    async def invalidate_health_state(self, health_refs: list[HealthRefInput]) -> bool:
        del health_refs
        return False

    async def get_health(self, health_ref: HealthRefInput) -> dict[str, Any]:
        resolved = coerce_health_ref(health_ref)
        return (await self.get_health_batch([resolved])).get(resolved.deployment_id, {})

    async def is_cooled_down(self, health_ref: HealthRefInput) -> bool:
        resolved = coerce_health_ref(health_ref)
        return (await self.get_cooldown_batch([resolved])).get(resolved.deployment_id, False)

    async def get_active_requests(self, deployment_id: str) -> int:
        return (await self.get_active_requests_batch([deployment_id])).get(deployment_id, 0)

    async def get_usage(self, deployment_id: str) -> dict[str, int]:
        return (await self.get_usage_batch([deployment_id])).get(deployment_id, {})

    async def get_latency_window(
        self, deployment_id: str, window_ms: int
    ) -> list[tuple[int, float]]:
        return (await self.get_latency_windows_batch([deployment_id], window_ms)).get(
            deployment_id, []
        )

    @property
    def captured_usage(self) -> Mapping[str, Mapping[str, int]]:
        return self._usage
