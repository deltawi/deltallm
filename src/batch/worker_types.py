from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from src.batch.embedding_microbatch import _ExecutionSignature
from src.models.requests import ChatCompletionRequest, EmbeddingRequest


class BatchArtifactValidationError(ValueError):
    """Raised when a completed batch artifact payload cannot be safely exposed."""


@dataclass
class BatchWorkerConfig:
    worker_id: str
    poll_interval_seconds: float = 1.0
    heartbeat_interval_seconds: float = 15.0
    job_lease_seconds: int = 120
    item_lease_seconds: int = 360
    finalization_retry_delay_seconds: int = 60
    worker_concurrency: int = 4
    item_buffer_multiplier: int = 2
    finalization_page_size: int = 500
    item_claim_limit: int = 20
    scheduler_claim_mode: Literal["job_fifo", "work_slice"] = "job_fifo"
    work_claim_max_items: int = 0
    work_claim_max_work_units: int = 0
    work_claim_min_items_for_microbatch: int = 4
    model_capacity_enabled: bool = False
    scheduler_shadow_enabled: bool = False
    tenant_fair_share_enabled: bool = False
    tenant_fair_share_base_quantum_work_units: int = 16
    tenant_fair_share_max_deficit_multiplier: int = 8
    tenant_max_in_flight_work_units: int = 0
    tenant_fair_share_disabled_model_groups: tuple[str, ...] = ()
    finalization_first: bool = True
    max_attempts: int = 3
    retry_initial_seconds: int = 5
    retry_max_seconds: int = 300
    retry_multiplier: float = 2.0
    retry_jitter: bool = True
    microbatch_retry_enabled: bool = True
    microbatch_max_group_retries: int = 2
    microbatch_min_reduced_size: int = 1
    microbatch_reduce_factor: float = 0.5
    completed_artifact_retention_days: int = 7
    failed_artifact_retention_days: int = 14


@dataclass
class _RequestShim:
    app: Any


@dataclass(slots=True)
class _PreparedEmbeddingItem:
    item: Any
    started_at_monotonic: float
    payload: EmbeddingRequest
    model_name: str
    model_group: str
    primary_deployment: Any
    request_context: dict[str, Any]
    failover_kwargs: dict[str, Any]
    request_shim: _RequestShim
    effective_upstream_max_batch_inputs: int
    microbatch_eligible: bool
    microbatch_ineligible_reason: str | None
    microbatch_weight: int | None
    execution_signature: _ExecutionSignature
    policy_auth: Any | None = None
    policy_lease: Any | None = None


@dataclass(slots=True)
class _PreparedChatItem:
    item: Any
    started_at_monotonic: float
    payload: ChatCompletionRequest
    model_name: str
    model_group: str
    primary_deployment: Any
    request_context: dict[str, Any]
    failover_kwargs: dict[str, Any]
    request_shim: _RequestShim
    policy_auth: Any | None = None
    policy_lease: Any | None = None
