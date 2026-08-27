from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request
from pydantic import ValidationError

from src.callbacks import CallbackManager
from src.middleware.rate_limit import (
    _release_rate_limits,
    acquire_parallel_limits_for_payload,
    check_and_acquire_rate_limits_for_payload,
)
from src.models.errors import InvalidRequestError
from src.models.requests import EmbeddingRequest
from src.routers.utils import enforce_budget_if_configured
from src.router.runtime_generation import RoutingRuntimeGeneration
from src.services.model_visibility import (
    ensure_model_allowed,
    get_callable_target_policy_mode_from_app,
    get_tier_policy_missing_service_mode_from_app,
    get_tier_policy_mode_from_app,
)
from src.services.preflight_capacity import acquire_preflight_capacity, release_preflight_capacity


@dataclass(frozen=True, slots=True)
class EmbeddingPreflightResult:
    auth: Any
    payload: EmbeddingRequest
    request_data: dict[str, Any]
    callback_manager: CallbackManager


async def run_embedding_preflight(
    *,
    request: Request,
    payload: EmbeddingRequest,
    routing_runtime: RoutingRuntimeGeneration,
) -> EmbeddingPreflightResult:
    prepared = getattr(request.state, "prepared_embedding_request", None)
    if isinstance(prepared, EmbeddingPreflightResult):
        return prepared

    auth = request.state.user_api_key
    await acquire_preflight_capacity(request, auth=auth)
    callback_manager: CallbackManager = getattr(
        request.app.state,
        "callback_manager",
        CallbackManager(),
    )
    request_data = payload.model_dump(exclude_none=True)
    request_data = await callback_manager.execute_pre_call_hooks(
        user_api_key_dict=auth.model_dump(mode="json"),
        cache=getattr(request.state, "cache_context", None),
        data=request_data,
        call_type="embedding",
    )
    request_data = await request.app.state.guardrail_middleware.run_pre_call(
        request_data=request_data,
        user_api_key_dict=auth.model_dump(mode="python"),
        call_type="embedding",
    )
    try:
        transformed_payload = EmbeddingRequest.model_validate(request_data)
    except ValidationError as exc:
        raise InvalidRequestError(
            message="Request data was invalid after pre-call policy processing"
        ) from exc
    transformed_data = transformed_payload.model_dump(exclude_none=True)

    ensure_model_allowed(
        auth,
        transformed_payload.model,
        callable_target_grant_service=getattr(
            request.app.state,
            "callable_target_grant_service",
            None,
        ),
        callable_target_grant_snapshot=routing_runtime.authorization_snapshot,
        tier_policy_service=getattr(request.app.state, "tier_policy_service", None),
        policy_mode=get_callable_target_policy_mode_from_app(request.app),
        tier_policy_mode=get_tier_policy_mode_from_app(request.app),
        tier_policy_missing_service_mode=get_tier_policy_missing_service_mode_from_app(request.app),
        emit_shadow_log=True,
    )
    await acquire_parallel_limits_for_payload(
        request,
        model=transformed_payload.model,
        payload=transformed_data,
    )
    try:
        await enforce_budget_if_configured(
            request,
            model=transformed_payload.model,
            auth=auth,
        )
    except Exception:
        await _release_rate_limits(request)
        await release_preflight_capacity(request)
        raise
    try:
        await check_and_acquire_rate_limits_for_payload(
            request,
            model=transformed_payload.model,
            payload=transformed_data,
        )
    finally:
        await release_preflight_capacity(request)

    result = EmbeddingPreflightResult(
        auth=auth,
        payload=transformed_payload,
        request_data=dict(transformed_data),
        callback_manager=callback_manager,
    )
    request.state.prepared_embedding_request = result
    return result
