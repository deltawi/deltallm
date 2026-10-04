"""Startup-only location and role of the Helm-owned allocation contract."""

from typing import Literal

from pydantic import BaseModel, Field


class DeploymentCapacitySettings(BaseModel):
    deployment_capacity_path: str | None = Field(default=None, min_length=1, max_length=1024)
    deployment_capacity_role: Literal["api", "batchWorker"] = "api"


def resolve_capacity_settings(
    general: BaseModel, environment: BaseModel
) -> DeploymentCapacitySettings:
    return DeploymentCapacitySettings.model_validate(
        {
            name: getattr(general if name in general.model_fields_set else environment, name)
            for name in DeploymentCapacitySettings.model_fields
        }
    )
