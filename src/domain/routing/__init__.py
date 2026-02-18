"""Canonical domain-routing namespace (compatibility layer)."""

from src.router import (
    BackgroundHealthChecker,
    CooldownManager,
    FallbackConfig,
    FailoverManager,
    HealthCheckConfig,
    HealthEndpointHandler,
    PassiveHealthTracker,
    RedisStateBackend,
    Router,
    RouterConfig,
    RoutingStrategy,
    build_deployment_registry,
)

__all__ = [
    "BackgroundHealthChecker",
    "CooldownManager",
    "FallbackConfig",
    "FailoverManager",
    "HealthCheckConfig",
    "HealthEndpointHandler",
    "PassiveHealthTracker",
    "RedisStateBackend",
    "Router",
    "RouterConfig",
    "RoutingStrategy",
    "build_deployment_registry",
]
