"""Startup bounds shared by file/environment and callback/guardrail lifecycles."""

from pydantic import BaseModel, Field


class RequestWorkSettings(BaseModel):
    callback_max_pending: int = Field(default=128, ge=1, le=4096)
    callback_max_concurrency: int = Field(default=4, ge=1, le=64)
    callback_max_payload_bytes: int = Field(default=262144, ge=1024, le=16777216)
    callback_max_bytes: int = Field(default=8388608, ge=1024, le=134217728)
    callback_timeout_seconds: float = Field(default=5, gt=0, le=60, allow_inf_nan=False)
    callback_shutdown_seconds: float = Field(default=5, gt=0, le=30, allow_inf_nan=False)
    guardrail_max_concurrency: int = Field(default=2, ge=1, le=32)
    guardrail_max_pending: int = Field(default=8, ge=1, le=256)
    guardrail_max_bytes: int = Field(default=33554432, ge=1024, le=268435456)
    guardrail_timeout_seconds: float = Field(default=5, gt=0, le=60, allow_inf_nan=False)
    guardrail_shutdown_seconds: float = Field(default=5, gt=0, le=30, allow_inf_nan=False)


def resolve_request_work_settings(
    general: BaseModel, environment: BaseModel
) -> RequestWorkSettings:
    return RequestWorkSettings.model_validate(
        {
            name: getattr(general if name in general.model_fields_set else environment, name)
            for name in RequestWorkSettings.model_fields
        }
    )
