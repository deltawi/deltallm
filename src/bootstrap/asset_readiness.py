"""Keep asset and realtime checks in the shared readiness inventory."""

from dataclasses import dataclass
from typing import Protocol

from starlette.datastructures import State

from src.readiness import HealthCheck
from src.realtime.runtime import RealtimeRuntime
from src.services.managed_asset_reconciliation import (
    ManagedAssetReconciliationHealth,
    ManagedAssetReconciliationService,
)

AUTHORIZATION_OWNERS = (
    "creator_model_access_service",
    "creator_route_group_access_service",
    "creator_prompt_access_service",
    "creator_mcp_access_service",
)


class AssetAuthorizer(Protocol):
    def authorization_ready(self) -> bool: ...


@dataclass(frozen=True)
class AssetHealthDetails:
    snapshot: ManagedAssetReconciliationHealth

    def payload(self) -> dict[str, str]:
        health = self.snapshot
        details = {
            "missing_links": str(health.missing_links),
            "kind_mismatches": str(health.kind_mismatches),
            "orphaned_policies": str(health.orphaned_policies),
            "last_repaired": str(health.last_repaired),
        }
        if health.last_checked_at is not None:
            details["last_checked_at"] = health.last_checked_at.isoformat()
        # Rebuild known diagnostics from counters. An exception message in the
        # service snapshot must not reach the health response.
        reasons = []
        if health.state == "failed":
            reasons.append("managed-asset link check failed")
        elif health.state == "starting":
            reasons.append("initial managed-asset link check has not completed")
        else:
            if health.missing_links:
                reasons.append(f"{health.missing_links} missing links remain")
            if health.kind_mismatches:
                reasons.append(f"{health.kind_mismatches} link type mismatches require review")
            if health.orphaned_policies:
                reasons.append(f"{health.orphaned_policies} orphaned policies require review")
        if reasons:
            details["detail"] = "; ".join(reasons)
        return details


def asset_link_check(state: State) -> HealthCheck:
    service: ManagedAssetReconciliationService | None = getattr(
        state, "managed_asset_reconciliation_service", None
    )
    if service is None:
        return HealthCheck(False, "unavailable")
    health = service.health_snapshot()
    if health.state not in {"starting", "ready", "degraded", "failed"}:
        return HealthCheck(False, "unavailable")
    return HealthCheck(health.ready, health.state, AssetHealthDetails(health))


def creator_authorization_check(state: State) -> HealthCheck:
    for attribute in AUTHORIZATION_OWNERS:
        service: AssetAuthorizer | None = getattr(state, attribute, None)
        if service is None:
            return HealthCheck(False, "unavailable")
        if not service.authorization_ready():
            return HealthCheck(False, "stale")
    return HealthCheck(True, "ready")


def realtime_check(state: State) -> HealthCheck:
    runtime: RealtimeRuntime | None = getattr(state, "realtime_runtime", None)
    ready = runtime is not None and runtime.ready
    return HealthCheck(ready, "ready" if ready else "unavailable")
