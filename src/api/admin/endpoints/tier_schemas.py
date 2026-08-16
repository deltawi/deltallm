from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class TierModelPolicyCreateRequest(TierModelPolicyRequest):
    expected_revision: int = Field(ge=0)


class TierModelPolicyPatchRequest(_StrictRequest):
    expected_revision: int = Field(ge=0)
    enabled: bool | None = None
    access_mode: str | None = None
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
    priority: int | None = None
    metadata: dict[str, Any] | None = None


class TierModelPolicyBulkLimitsRequest(_StrictRequest):
    expected_revision: int = Field(ge=0)
    rpm_limit: int | None = Field(default=None, ge=1)
    tpm_limit: int | None = Field(default=None, ge=1)
    policy_ids: list[str] | None = None
    all_filtered: bool = False
    search: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None
    access_mode: str | None = Field(default=None, max_length=40)
    capacity_pool_key: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_bulk_target_and_values(self) -> TierModelPolicyBulkLimitsRequest:
        supplied = self.model_fields_set
        if "rpm_limit" not in supplied and "tpm_limit" not in supplied:
            raise ValueError("rpm_limit or tpm_limit is required")
        if self.policy_ids:
            if self.all_filtered:
                raise ValueError("choose policy_ids or all_filtered, not both")
            if any(not str(policy_id or "").strip() for policy_id in self.policy_ids):
                raise ValueError("policy_ids must contain nonblank IDs")
        elif not self.all_filtered:
            raise ValueError("policy_ids or all_filtered=true is required")
        return self


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


class TierCapacityPoolCreateRequest(TierCapacityPoolRequest):
    expected_revision: int = Field(ge=0)


class TierCapacityPoolPatchRequest(_StrictRequest):
    expected_revision: int = Field(ge=0)
    rpm_capacity: int | None = Field(default=None, ge=1)
    tpm_capacity: int | None = Field(default=None, ge=1)
    max_parallel_requests: int | None = Field(default=None, ge=1)
    strategy: str | None = None
    saturation_threshold: float | None = Field(default=None, gt=0, le=1)
    burst_multiplier: float | None = Field(default=None, ge=1)
    metadata: dict[str, Any] | None = None


class TierConfigurationMutationRequest(_StrictRequest):
    expected_revision: int = Field(ge=0)


class TierActivationRequest(_StrictRequest):
    expected_revision: int = Field(ge=0)
    expected_active_version_id: str | None
