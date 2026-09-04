from __future__ import annotations

from src.models.errors import RoutingFailureAction
from src.router.health_policy import affects_deployment_health


def routing_failure_action(exc: Exception) -> RoutingFailureAction:
    """Resolve retry/failover behavior without conflating it with health state."""

    explicit = getattr(exc, "routing_failure_action", None)
    if isinstance(explicit, RoutingFailureAction):
        return explicit
    if affects_deployment_health(exc):
        return RoutingFailureAction.RETRY_OR_NEXT
    return RoutingFailureAction.FAIL_FAST
