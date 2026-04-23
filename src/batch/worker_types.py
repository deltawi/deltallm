from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.batch.embedding_microbatch import _ExecutionSignature
from src.models.requests import EmbeddingRequest


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
    max_attempts: int = 3
    retry_initial_seconds: int = 5
    retry_max_seconds: int = 300
    retry_multiplier: float = 2.0
    retry_jitter: bool = True
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
