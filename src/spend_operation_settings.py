"""Startup-only cutover and allocation settings for ordinary spend recovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from src.redis_runtime import startup_setting


if TYPE_CHECKING:
    from src.config import GeneralSettings, Settings


class SpendOperationSettings(BaseModel):
    spend_operation_intents_enabled: bool = False
    spend_settlement_db_pool_size: int = Field(default=1, ge=1, le=99)


@dataclass(frozen=True, slots=True)
class SpendOperationAllocation:
    enabled: bool
    settlement_connections: int

    @classmethod
    def resolve(
        cls, general: GeneralSettings, settings: Settings, *, telemetry_connections: int
    ) -> SpendOperationAllocation:
        enabled = bool(startup_setting(general, settings, "spend_operation_intents_enabled", False))
        size = int(startup_setting(general, settings, "spend_settlement_db_pool_size", 1))
        if enabled and (
            startup_setting(general, settings, "spend_ingestion_mode", "legacy") != "outbox"
            or not startup_setting(general, settings, "spend_ingestion_worker_enabled", True)
            or size >= telemetry_connections
        ):
            raise ValueError(
                "Spend operation intents require outbox, its worker, and telemetry connections "
                "for both admission and settlement"
            )
        return cls(enabled, size if enabled else 0)
