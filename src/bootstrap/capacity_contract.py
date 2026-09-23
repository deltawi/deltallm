"""Validate Helm allocations at bootstrap, before opening owned dependencies."""

from __future__ import annotations

from dataclasses import dataclass
import resource

from src.config import AppConfig, Settings
from src.capacity_runtime_policy import CapacityRuntimePolicy
from src.deployment_capacity_report import CapacityReport, PoolAllocation
from src.deployment_capacity_settings import DeploymentCapacitySettings, resolve_capacity_settings
from src.redis_runtime import redis_connection_options
from src.upstream_http import (
    CONTROL_HTTP_MAX_CONNECTIONS,
    build_upstream_http_limits,
    environment_proxy_pool_count,
)


@dataclass(frozen=True)
class DeploymentCapacityContract:
    settings: DeploymentCapacitySettings
    report: CapacityReport
    policy: CapacityRuntimePolicy

    @classmethod
    def load(cls, config: AppConfig, settings: Settings) -> DeploymentCapacityContract | None:
        allocation = resolve_capacity_settings(config.general_settings, settings)
        if allocation.deployment_capacity_path is None:
            return None
        report = CapacityReport.read(allocation.deployment_capacity_path)
        if not report.extended or allocation.deployment_capacity_role not in report.roles:
            raise RuntimeError("Deployment capacity report does not govern this process role")
        policy = CapacityRuntimePolicy.from_general(config.general_settings)
        if report.production:
            policy.validate_production()
        return cls(allocation, report, policy)

    def validate(
        self, config: AppConfig, settings: Settings, *, database: int, critical: int, cache: int
    ) -> None:
        if resolve_capacity_settings(config.general_settings, settings) != self.settings:
            raise RuntimeError("Deployment capacity settings require a restart")
        if CapacityRuntimePolicy.from_general(config.general_settings) != self.policy:
            raise RuntimeError("Deployment capacity policy requires a restart")
        role = self.report.roles[self.settings.deployment_capacity_role]
        proxies = environment_proxy_pool_count()
        if proxies != self.report.proxy_pools_per_client:
            raise RuntimeError("Proxy transport count differs from deployment capacity allocation")
        limits = build_upstream_http_limits(config.general_settings)
        actual = PoolAllocation(
            postgresql=database,
            redis_critical=critical,
            redis_cache=cache,
            upstream_http=(limits.max_connections or 0) * (1 + proxies),
            control_http=CONTROL_HTTP_MAX_CONNECTIONS * (1 + proxies),
            # Optional SDK/callback transports are an explicit operator reserve,
            # not a counted application-owned pool. Do not claim runtime parity.
            auxiliary_http=0,
        )
        if actual != role.pools.model_copy(update={"auxiliary_http": 0}):
            raise RuntimeError("Runtime pools differ from deployment capacity allocation")
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        needed = max(role.file_descriptors.python, role.file_descriptors.engine)
        if soft_limit != resource.RLIM_INFINITY and soft_limit < needed:
            raise RuntimeError("Process file descriptor limit is below deployment allocation")
        self._validate_redis_domain(config, settings)

    def _validate_redis_domain(self, config: AppConfig, settings: Settings) -> None:
        primary = redis_connection_options(settings, config.general_settings, "critical")
        bulk = redis_connection_options(settings, config.general_settings, "bulk")

        def domain(options: dict[str, object]) -> tuple[object, ...]:
            if options.get("path") is not None:
                return ("unix", options["path"])
            # Match redis-py's defaults; different logical DBs share maxclients.
            return (str(options.get("host", "localhost")).lower(), options.get("port", 6379))

        separate = domain(primary) != domain(bulk)
        if separate != self.report.cache_redis.separate:
            raise RuntimeError("Redis endpoint mapping differs from deployment capacity domains")
