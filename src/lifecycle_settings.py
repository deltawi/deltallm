"""Startup-only process budgets, shared by the launcher and application."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class LifecycleSettings(BaseModel):
    readiness_probe_timeout_seconds: float = Field(default=1, gt=0, le=2, allow_inf_nan=False)
    readiness_cache_seconds: float = Field(default=1, gt=0, le=5, allow_inf_nan=False)
    lifecycle_withdrawal_seconds: float = Field(default=5, ge=0, le=30, allow_inf_nan=False)
    lifecycle_request_drain_seconds: float = Field(default=45, gt=0, le=600, allow_inf_nan=False)
    lifecycle_cancellation_seconds: float = Field(default=5, gt=0, le=30, allow_inf_nan=False)
    lifecycle_worker_drain_seconds: float = Field(default=20, gt=0, le=60, allow_inf_nan=False)
    lifecycle_close_seconds: float = Field(default=5, gt=0, le=30, allow_inf_nan=False)
    lifecycle_shutdown_seconds: float = Field(default=80, gt=0, le=750, allow_inf_nan=False)
    migration_mode: Literal["startup", "external"] = "startup"
    migration_verify_timeout_seconds: float = Field(default=10, gt=0, le=60, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_shutdown_budget(self) -> "LifecycleSettings":
        phases = (
            self.lifecycle_withdrawal_seconds
            + self.lifecycle_request_drain_seconds
            + self.lifecycle_cancellation_seconds
            + self.lifecycle_worker_drain_seconds
            + self.lifecycle_close_seconds
        )
        if phases > self.lifecycle_shutdown_seconds + 1e-9:
            raise ValueError("lifecycle_shutdown_seconds must reserve every shutdown phase")
        return self


def resolve_lifecycle_settings(general: BaseModel, environment: BaseModel) -> LifecycleSettings:
    return LifecycleSettings.model_validate(
        {
            name: getattr(general if name in general.model_fields_set else environment, name)
            for name in LifecycleSettings.model_fields
        }
    )
