from __future__ import annotations

from dataclasses import dataclass

from src.models.responses import UserAPIKeyAuth
from src.services.output_limit_types import output_scopes
from src.rate_limit_policy import (
    RateLimitLease,
    acquire_parallel_limit_controls,
    acquire_rate_limit_controls,
    build_rate_limit_checks,
    release_rate_limit_controls,
)
from src.realtime.config import RealtimeSettings
from src.realtime.errors import RealtimeError
from src.realtime.routing import RealtimeRoute, RealtimeRouting
from src.router.candidates import AttemptCapacity, AttemptCapacityLimit, AttemptPermit
from src.router.strategies import usage_limits_for_deployment
from src.services.limit_counter import LimitCounter, ParallelLimitCheck, ParallelLimitLease
from src.tier_rate_limit_policy import build_tier_limit_controls


@dataclass(frozen=True, slots=True)
class RealtimeCapacity:
    limiter: LimitCounter
    routing: RealtimeRouting
    settings: RealtimeSettings
    fair_share_enabled: bool = False

    def policy(self, auth: UserAPIKeyAuth, model: str) -> dict:
        settings = self.routing.generations.require_snapshot().app_config.general_settings
        fair_share = (
            settings.tier_capacity_fair_share_enabled
            if "tier_capacity_fair_share_enabled" in settings.model_fields_set
            else self.fair_share_enabled
        )
        return dict(
            auth=auth,
            tokens=0,
            model=model,
            tier_policy_service=self.routing.tiers,
            tier_policy_mode=self.routing.tier_policy_mode,
            tier_policy_missing_service_mode="fail_closed",
            tier_capacity_fair_share_enabled=fair_share,
        )

    def require_supported(self, auth: UserAPIKeyAuth, route: RealtimeRoute) -> None:
        if auth.max_parallel_requests is not None and auth.max_parallel_requests > 0:
            raise RealtimeError(
                "parallel_profile_unsupported",
                "Legacy key concurrency limits are not yet qualified for Realtime",
            )
        policy = {**self.policy(auth, route.target.public_model), "tokens": 1}
        controls = build_tier_limit_controls(**policy)
        checks = build_rate_limit_checks(**policy)
        checks.extend(item.rate_check for item in controls.capacity_rate_checks)
        if (
            bool(output_scopes(auth))
            or any(check.scope.endswith(("_tpm", "_tpm_limit", "_tpd")) for check in checks)
            or any(check.tpm_capacity for check in controls.fair_share_checks)
            or any(counter != "rpm" for counter, _ in usage_limits_for_deployment(route.deployment))
            or any(
                limit is not None and limit > 0
                for limit in (
                    route.deployment.audio_seconds_pm_limit,
                    route.deployment.char_pm_limit,
                )
            )
        ):
            raise RealtimeError(
                "token_quota_profile_unsupported",
                "Token and audio quotas require a qualified Realtime allowance profile",
            )

    async def acquire(self, auth: UserAPIKeyAuth, route: RealtimeRoute) -> RealtimeLeases:
        self.require_supported(auth, route)
        checks = [
            ParallelLimitCheck("realtime_global", "global", self.settings.global_max_connections)
        ]
        if auth.organization_id:
            checks.append(
                ParallelLimitCheck(
                    "realtime_org", auth.organization_id, self.settings.organization_max_connections
                )
            )
        owned = await self.limiter.acquire_parallel_leases(checks, ttl_seconds=self.ttl)
        try:
            parallel = await acquire_parallel_limit_controls(
                limiter=self.limiter, **self.policy(auth, route.target.public_model)
            )
        except BaseException:
            await self.limiter.release_parallel_leases(list(owned))
            raise
        return RealtimeLeases(self, route, owned, parallel, self.parallel_signature(auth, route))

    def parallel_signature(self, auth: UserAPIKeyAuth, route: RealtimeRoute) -> tuple:
        controls = build_tier_limit_controls(**self.policy(auth, route.target.public_model))
        return tuple(
            (check.scope, check.entity_id, check.limit) for check in controls.parallel_checks
        )

    @property
    def ttl(self) -> int:
        return max(30, int(self.settings.health_seconds * 4))


@dataclass(slots=True)
class RealtimeLeases:
    capacity: RealtimeCapacity
    route: RealtimeRoute
    owned: tuple[ParallelLimitLease, ...]
    parallel: RateLimitLease
    signature: tuple

    async def refresh(self) -> None:
        limiter = self.capacity.limiter
        await limiter.refresh_parallel_leases(
            [*self.owned, *self.parallel.parallel_leases],
            ttl_seconds=self.capacity.ttl,
            require_owned=True,
        )
        if self.parallel.legacy_parallel_lease is not None:
            await limiter.refresh_legacy_parallel_lease(
                self.parallel.legacy_parallel_lease,
                ttl_seconds=self.capacity.ttl,
                require_owned=True,
            )

    async def turn(self, auth: UserAPIKeyAuth) -> AttemptPermit:
        self.capacity.require_supported(auth, self.route)
        await acquire_rate_limit_controls(
            limiter=self.capacity.limiter,
            preacquired_parallel_lease=self.parallel,
            **self.capacity.policy(auth, self.route.target.public_model),
        )
        capacity = AttemptCapacity(
            limits=tuple(
                AttemptCapacityLimit(counter, limit, 1)
                for counter, limit in usage_limits_for_deployment(self.route.deployment)
            ),
            require_shared=True,
        )
        permit = await self.route.generation.router.state.acquire_attempt(
            self.route.deployment.health_ref,
            capacity,
            lease_ttl_seconds=int(
                self.capacity.settings.session_seconds + self.capacity.settings.cleanup_seconds + 30
            ),
        )
        if not permit.acquired or permit.backend != "redis":
            raise RealtimeError("capacity_exceeded", "Realtime deployment capacity is unavailable")
        return permit

    async def close(self) -> None:
        try:
            await release_rate_limit_controls(limiter=self.capacity.limiter, lease=self.parallel)
        finally:
            await self.capacity.limiter.release_parallel_leases(list(self.owned))
