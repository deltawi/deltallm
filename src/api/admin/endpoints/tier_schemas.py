from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TierCreateRequest(_StrictRequest):
    tier_key: str
    name: str
    description: str | None = None
    enabled: bool = True
    metadata: dict[str, Any] | None = None


class TierPatchRequest(_StrictRequest):
    tier_key: str | None = None
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    metadata: dict[str, Any] | None = None


class TierVersionCreateRequest(_StrictRequest):
    version_number: int | None = Field(default=None, ge=1)
    metadata: dict[str, Any] | None = None


class TierModelPolicyRequest(_StrictRequest):
    callable_key: str
    enabled: bool = True
    access_mode: str = "allow"
    rpm_limit: int | None = Field(default=None, ge=1)
    tpm_limit: int | None = Field(default=None, ge=1)
    rph_limit: int | None = Field(default=None, ge=1)
    rpd_limit: int | None = Field(default=None, ge=1)
    tpd_limit: int | None = Field(default=None, ge=1)
    max_parallel_requests: int | None = Field(default=None, ge=1)
    batch_rpm_limit: int | None = Field(default=None, ge=1)
    batch_tpm_limit: int | None = Field(default=None, ge=1)
    pricing: dict[str, Any] | None = None
    capacity_pool_key: str | None = None
    priority: int = 0
    metadata: dict[str, Any] | None = None


class TierModelPolicyReplaceRequest(_StrictRequest):
    policies: list[TierModelPolicyRequest]


class TierCapacityPoolRequest(_StrictRequest):
    pool_key: str
    callable_key: str
    rpm_capacity: int | None = Field(default=None, ge=1)
    tpm_capacity: int | None = Field(default=None, ge=1)
    max_parallel_requests: int | None = Field(default=None, ge=1)
    strategy: str = "hard_cap"
    saturation_threshold: float | None = Field(default=None, gt=0, le=1)
    burst_multiplier: float | None = Field(default=None, ge=1)
    metadata: dict[str, Any] | None = None


class TierCapacityPoolReplaceRequest(_StrictRequest):
    pools: list[TierCapacityPoolRequest]


class TierCapacityBoostRequest(_StrictRequest):
    pool_key: str
    callable_key: str
    organization_id: str
    multiplier: float = Field(ge=1, le=100)
    expires_in_seconds: int = Field(ge=1, le=604_800)
