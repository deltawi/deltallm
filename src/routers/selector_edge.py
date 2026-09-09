from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from uuid import UUID

from fastapi import Request

from src.billing.operation_reservation import BillingOperationUnavailable
from src.cache.execution_eligibility import ResponseCacheEligibility
from src.models.requests import ChatCompletionRequest
from src.models.errors import ProxyError, RoutingFailureAction
from src.models.responses import UserAPIKeyAuth
from src.router.execution import RequestDeadline
from src.router.runtime_generation import RoutingRuntimeGeneration
from src.router.selection.contracts import SelectorDecision
from src.router.selection.operation import (
    SelectorOperation,
    SelectorPrincipal,
    bind_selector_preparation,
)
from src.router.selection.request_context import set_request_selector_state
from src.router.selection.request_state import RequestSelectorState
from src.router.selection.runtime import SelectorExecutionFactory
from src.routers.routing_decision import attach_selector_decision
from src.services.model_visibility import (
    ensure_model_allowed,
    get_callable_target_policy_mode_from_app,
    get_tier_policy_mode_from_app,
    get_tier_policy_missing_service_mode_from_app,
)
from src.telemetry.event_identity import get_or_create_billing_event_id
from src.telemetry.selector_decision import ProtectedSelectorDecision


def bind_selector_operation(
    request: Request,
    *,
    runtime: RoutingRuntimeGeneration,
    payload: ChatCompletionRequest,
    auth: UserAPIKeyAuth,
    context: dict[str, object],
    token_estimate: int,
) -> RequestDeadline | None:
    """Transport composition after normalized preflight and whole-response cache miss."""
    group = runtime.router.resolve_model_group(payload.model)
    if group not in runtime.selector_reachable_groups:
        return None
    cache = getattr(request.state, "response_cache_eligibility", None)
    factory = getattr(request.app.state, "selector_execution_factory", None)
    if not isinstance(cache, ResponseCacheEligibility) or not isinstance(
        factory, SelectorExecutionFactory
    ):
        raise BillingOperationUnavailable()
    cache.require_provider_execution()
    policy = runtime.router.config.route_group_policies.get(group)
    deadline = runtime.failover_manager.create_request_deadline(
        policy.timeout_seconds if policy else None
    )
    state = RequestSelectorState(deadline)
    set_request_selector_state(context, state)
    operation = SelectorOperation(
        state=state,
        selectors=runtime.selectors,
        factory=factory,
        principal=SelectorPrincipal(
            api_key=auth.api_key,
            user_id=auth.user_id,
            team_id=auth.team_id,
            organization_id=auth.organization_id,
            owner_account_id=auth.owner_account_id,
        ),
        operation_id=UUID(get_or_create_billing_event_id(request)),
        model=payload.model,
        payload=payload,
        token_estimate=token_estimate,
        cache=cache,
        capacity_owner=runtime.router.state,
        authorize=lambda target: _authorize(request, runtime, auth, target),
        observe=lambda decision: attach_selector_decision(
            request, ProtectedSelectorDecision.from_decision(decision)
        ),
        run_connected=lambda work: _while_connected(request, work),
        context=context,
        planner=runtime.router,
    )
    bind_selector_preparation(context, operation)
    return deadline


def _authorize(
    request: Request, runtime: RoutingRuntimeGeneration, auth: UserAPIKeyAuth, target: str
) -> None:
    ensure_model_allowed(
        auth,
        target,
        callable_target_grant_service=getattr(
            request.app.state, "callable_target_grant_service", None
        ),
        callable_target_grant_snapshot=runtime.authorization_snapshot,
        tier_policy_service=getattr(request.app.state, "tier_policy_service", None),
        policy_mode=get_callable_target_policy_mode_from_app(request.app),
        tier_policy_mode=get_tier_policy_mode_from_app(request.app),
        tier_policy_missing_service_mode=get_tier_policy_missing_service_mode_from_app(request.app),
    )


class SelectorClientDisconnectedError(ProxyError):
    status_code = 499
    error_type = "client_disconnected"
    message = "Client disconnected"


async def _disconnected(request: Request) -> None:
    # The admitted body has already been fully consumed. A blocking receive is
    # required: is_disconnected's immediate cancellation misses middleware checkpoints.
    while (await request.receive())["type"] != "http.disconnect":
        pass


async def _while_connected(request: Request, work: Awaitable[SelectorDecision]) -> SelectorDecision:
    """Two bounded request-owned tasks; both are joined, never detached."""
    task = asyncio.ensure_future(work)
    disconnect = asyncio.create_task(_disconnected(request))
    try:
        done, _ = await asyncio.wait((task, disconnect), return_when=asyncio.FIRST_COMPLETED)
        if disconnect in done:
            await disconnect
            task.cancel()
            # Cancel the provider task, but unwind middleware with a normal typed
            # failure. Raising CancelledError inside BaseHTTPMiddleware's child can
            # otherwise leave the outer request waiting forever for a response.
            raise SelectorClientDisconnectedError(
                code="client_disconnected",
                affects_deployment_health=False,
                routing_failure_action=RoutingFailureAction.FAIL_FAST,
            )
        return await task
    finally:
        for child in (task, disconnect):
            if not child.done():
                child.cancel()
        await asyncio.gather(task, disconnect, return_exceptions=True)
