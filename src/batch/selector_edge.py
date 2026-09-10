from __future__ import annotations

from fastapi import FastAPI

from src.batch.models import BatchItemRecord, BatchJobRecord
from src.batch.policy import BatchPreflightResult
from src.batch.repositories.selector_repository import BatchSelectorRepository
from src.batch.repository import BatchRepository
from src.batch.selector_checkpoint import BatchSelectorUnavailable
from src.batch.selector_execution import BatchSelectorExecution
from src.batch.worker_types import BatchRoutingRuntime
from src.models.requests import ChatCompletionRequest
from src.router.runtime_generation import RoutingRuntimeGeneration
from src.router.selection.runtime import SelectorExecutionFactory
from src.services.model_visibility import (
    ensure_model_allowed,
    get_callable_target_policy_mode_from_app,
    get_tier_policy_missing_service_mode_from_app,
    get_tier_policy_mode_from_app,
)


def compose_batch_selector(
    *,
    app: FastAPI,
    repository: BatchRepository,
    runtime: BatchRoutingRuntime,
    job: BatchJobRecord,
    item: BatchItemRecord,
    worker_id: str,
    preflight: BatchPreflightResult,
    payload: ChatCompletionRequest,
    context: dict[str, object],
) -> BatchSelectorExecution:
    """Worker composition edge; no application state crosses into the selector."""
    factory = getattr(app.state, "selector_execution_factory", None)
    if (
        not preflight.auth_verified
        or preflight.auth.api_key != job.created_by_api_key
        or not isinstance(runtime, RoutingRuntimeGeneration)
        or not isinstance(factory, SelectorExecutionFactory)
        or repository.prisma is None
        or getattr(app.state, "budget_service", None) is None
        or getattr(app.state, "limit_counter", None) is None
    ):
        raise BatchSelectorUnavailable()
    factory.require_ready()
    grants = getattr(app.state, "callable_target_grant_service", None)
    tiers = getattr(app.state, "tier_policy_service", None)
    policy_mode = get_callable_target_policy_mode_from_app(app)
    tier_mode = get_tier_policy_mode_from_app(app)
    missing_mode = get_tier_policy_missing_service_mode_from_app(app)
    return BatchSelectorExecution(
        runtime=runtime,
        factory=factory,
        checkpoints=BatchSelectorRepository(repository.prisma),
        job=job,
        item=item,
        worker_id=worker_id,
        payload=payload,
        token_estimate=preflight.token_estimate,
        context=context,
        authorize=lambda target: ensure_model_allowed(
            preflight.auth,
            target,
            callable_target_grant_service=grants,
            callable_target_grant_snapshot=runtime.authorization_snapshot,
            tier_policy_service=tiers,
            policy_mode=policy_mode,
            tier_policy_mode=tier_mode,
            tier_policy_missing_service_mode=missing_mode,
        ),
    )
