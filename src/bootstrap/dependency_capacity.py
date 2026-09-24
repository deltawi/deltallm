"""Keep durable configuration inside the deployment's startup allocation budget."""

from dataclasses import dataclass
from typing import Self

from src.config import (
    AppConfig,
    Settings,
    resolve_database_settings,
    resolve_telemetry_database_settings,
)
from src.spend_operation_settings import SpendOperationAllocation
from src.database_settings import DatabaseAllocationSettings
from src.db.allocation_config import resolve_allocation_settings
from src.redis_runtime import RedisLimits, startup_setting
from src.bootstrap.capacity_contract import DeploymentCapacityContract


@dataclass(frozen=True)
class DependencyAllocationSnapshot:
    database: DatabaseAllocationSettings
    redis: RedisLimits
    control_connections: int
    telemetry_connections: int
    spend_operations: SpendOperationAllocation
    deployment: DeploymentCapacityContract | None = None

    @classmethod
    def build(cls, config: AppConfig, settings: Settings) -> Self:
        general = config.general_settings
        database = resolve_database_settings(config, settings)
        if database is None:
            raise RuntimeError("Database allocations require an explicit database URL")
        telemetry_enabled = any(
            startup_setting(general, settings, field, "legacy") == "outbox"
            for field in ("audit_ingestion_mode", "spend_ingestion_mode")
        )
        telemetry = (
            resolve_telemetry_database_settings(config, settings) if telemetry_enabled else None
        )
        return cls(
            database=resolve_allocation_settings(general, settings),
            redis=RedisLimits.from_settings(general, settings),
            control_connections=database.pool_size,
            telemetry_connections=telemetry.pool_size if telemetry is not None else 0,
            spend_operations=SpendOperationAllocation.resolve(
                general, settings, telemetry_connections=telemetry.pool_size if telemetry else 0
            ),
            deployment=DeploymentCapacityContract.load(config, settings),
        )

    def validate_deployment(self, config: AppConfig, settings: Settings) -> None:
        if self.deployment is not None:
            database = self.control_connections + self.database.db_foreground_pool_size
            if self.telemetry_connections:
                database += self.telemetry_connections + self.database.telemetry_worker_db_pool_size
            self.deployment.validate(
                config,
                settings,
                database=database,
                critical=self.redis.critical_max_connections,
                cache=self.redis.cache_max_connections + self.redis.bulk_max_connections,
            )

    def validate_effective(self, config: AppConfig, settings: Settings) -> None:
        if self != self.build(config, settings):
            # Do not include config values: other fields can contain credentials.
            raise RuntimeError(
                "Dependency allocation settings must match startup file/environment configuration"
            )
        self.validate_deployment(config, settings)
