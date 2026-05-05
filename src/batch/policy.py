from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeVar

from pydantic import BaseModel

from src.batch.endpoints import batch_call_type_for_endpoint
from src.batch.retry import classify_batch_retry
from src.callbacks import CallbackManager
from src.rate_limit_policy import (
    RateLimitLease,
    acquire_rate_limit_controls,
    estimate_tokens,
    release_rate_limit_controls,
)
from src.metrics import (
    increment_batch_policy_allowed,
    increment_batch_policy_rejected,
    increment_batch_policy_retryable_failure,
    observe_batch_preflight_latency,
)
from src.models.responses import UserAPIKeyAuth
from src.services.model_visibility import ensure_model_allowed, get_callable_target_policy_mode_from_app

logger = logging.getLogger(__name__)

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
TRequest = TypeVar("TRequest", bound=BaseModel)


@dataclass(slots=True)
class BatchPolicyLease:
    rate_limit_lease: RateLimitLease


@dataclass(frozen=True, slots=True)
class BatchPreflightResult:
    payload: Any
    request_data: dict[str, Any]
    auth: UserAPIKeyAuth


def _looks_like_api_key_hash(value: str | None) -> bool:
    return bool(value and _SHA256_HEX_RE.fullmatch(str(value).strip()))


def _fallback_auth_for_job(job: Any) -> UserAPIKeyAuth:
    return UserAPIKeyAuth(
        api_key=str(getattr(job, "created_by_api_key", None) or f"batch:{getattr(job, 'batch_id', 'unknown')}"),
        user_id=getattr(job, "created_by_user_id", None),
        team_id=getattr(job, "created_by_team_id", None),
        organization_id=getattr(job, "created_by_organization_id", None),
    )


async def resolve_batch_job_auth(app: Any, job: Any) -> UserAPIKeyAuth:
    token_hash = getattr(job, "created_by_api_key", None)
    key_service = getattr(app.state, "key_service", None)
    if key_service is not None and _looks_like_api_key_hash(token_hash):
        loader = getattr(key_service, "get_auth_by_token_hash", None)
        if callable(loader):
            return await loader(str(token_hash))
    return _fallback_auth_for_job(job)


def record_batch_policy_failure(*, endpoint: str, exc: Exception) -> None:
    decision = classify_batch_retry(exc)
    reason = str(getattr(exc, "code", None) or getattr(exc, "param", None) or decision.category.value)
    if decision.retryable:
        increment_batch_policy_retryable_failure(endpoint=endpoint, reason=reason)
    else:
        increment_batch_policy_rejected(endpoint=endpoint, reason=reason)


async def run_batch_request_preflight(
    *,
    app: Any,
    job: Any,
    payload: TRequest,
    request_data: dict[str, Any],
    call_type: str,
) -> BatchPreflightResult:
    endpoint = batch_call_type_for_endpoint(str(getattr(job, "endpoint", "")))
    started = perf_counter()
    auth = await resolve_batch_job_auth(app, job)
    try:
        grant_service = getattr(app.state, "callable_target_grant_service", None)
        ensure_model_allowed(
            auth,
            str(getattr(payload, "model", "")),
            callable_target_grant_service=grant_service,
            policy_mode=get_callable_target_policy_mode_from_app(app),
            emit_shadow_log=True,
        )

        budget_service = getattr(app.state, "budget_service", None)
        if budget_service is not None:
            await budget_service.check_budgets(
                api_key=getattr(job, "created_by_api_key", None),
                user_id=auth.user_id,
                team_id=auth.team_id,
                organization_id=auth.organization_id,
                model=str(getattr(payload, "model", "")),
            )

        callback_manager: CallbackManager = getattr(app.state, "callback_manager", None) or CallbackManager()
        data = await callback_manager.execute_pre_call_hooks(
            user_api_key_dict=auth.model_dump(mode="json"),
            cache=None,
            data=dict(request_data),
            call_type=call_type,
        )

        guardrail_middleware = getattr(app.state, "guardrail_middleware", None)
        if guardrail_middleware is not None:
            data = await guardrail_middleware.run_pre_call(
                request_data=data,
                user_api_key_dict=auth.model_dump(mode="python"),
                call_type=call_type,
            )

        transformed_payload = payload.__class__.model_validate(data)
    except Exception as exc:
        decision = classify_batch_retry(exc)
        record_batch_policy_failure(endpoint=endpoint, exc=exc)
        if decision.retryable:
            status = "retryable_failure"
        else:
            status = "rejected"
        observe_batch_preflight_latency(endpoint=endpoint, status=status, latency_seconds=perf_counter() - started)
        raise

    increment_batch_policy_allowed(endpoint=endpoint)
    observe_batch_preflight_latency(endpoint=endpoint, status="allowed", latency_seconds=perf_counter() - started)
    return BatchPreflightResult(payload=transformed_payload, request_data=data, auth=auth)


async def acquire_batch_policy_lease(*, app: Any, payload: BaseModel, auth: UserAPIKeyAuth) -> BatchPolicyLease | None:
    limiter = getattr(app.state, "limit_counter", None)
    if limiter is None:
        return None
    data = payload.model_dump(exclude_none=True)
    lease, _state = await acquire_rate_limit_controls(
        limiter=limiter,
        auth=auth,
        tokens=estimate_tokens(data),
        model=str(getattr(payload, "model", "") or ""),
    )
    return BatchPolicyLease(rate_limit_lease=lease)


async def release_batch_policy_lease(*, app: Any, lease: BatchPolicyLease | None) -> None:
    if lease is None:
        return
    limiter = getattr(app.state, "limit_counter", None)
    if limiter is None:
        return
    try:
        await release_rate_limit_controls(limiter=limiter, lease=lease.rate_limit_lease)
    except Exception as exc:
        logger.warning("batch policy rate-limit release failed error=%s", exc, exc_info=True)
