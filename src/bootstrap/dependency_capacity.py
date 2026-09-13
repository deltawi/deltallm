"""Keep durable configuration inside the deployment's startup allocation budget."""

from dataclasses import dataclass
from typing import Self

from src.config import (
    AppConfig,
    Settings,
    resolve_database_settings,
    resolve_telemetry_database_settings,
)
from src.database_settings import DatabaseAllocationSettings
from src.db.allocation_config import resolve_allocation_settings
from src.redis_runtime import RedisLimits, startup_setting


@dataclass(frozen=True)
class DependencyAllocationSnapshot:
    database: DatabaseAllocationSettings
    redis: RedisLimits
    control_connections: int
    telemetry_connections: int

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
        )

    def validate_effective(self, config: AppConfig, settings: Settings) -> None:
        if self != self.build(config, settings):
            # Do not include config values: other fields can contain credentials.
            raise RuntimeError(
                "Dependency allocation settings must match startup file/environment configuration"
            )
