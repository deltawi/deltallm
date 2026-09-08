import asyncio
from unittest.mock import AsyncMock

import pytest

from src.billing.operation_reservation import (
    BillingOperationUnavailable,
    ComponentState,
    ReservedOperation,
)
from src.cache.execution_eligibility import ResponseCacheEligibility, ResponseCacheOutcome
from src.models.requests import ChatCompletionRequest
from src.router.candidates import AttemptPermit
from src.router.execution import RequestDeadline
from src.router.health_state import DeploymentHealthRef
from src.router.selection.capacity import SelectorCapacityBounds
from src.router.selection.contracts import (
    SelectorHopSuccess,
    ReportedSelectorUsage,
    UnknownSelectorUsage,
)
from src.router.selection.prerequisites import admitted_selector_service
from src.router.selection.request_state import RequestSelectorState
from tests.test_operation_reservation import make_operation


def execution(policy, identity, *, cache=ResponseCacheOutcome.MISS):
    operation = make_operation()
    policy = policy.model_copy(
        update={"classifier_deployment_id": operation.attribution.deployment_id}
    )
    billing, capacity, provider = AsyncMock(), AsyncMock(), AsyncMock()
    billing.reserve.return_value = ReservedOperation(
        operation=operation,
        selector_state=ComponentState.RESERVED,
        answer_state=ComponentState.RESERVED,
    )

    async def acquire(ref, bounds, **kwargs):
        return AttemptPermit(ref.deployment_id, ref, True, "redis", bounds.owner_token)

    capacity.acquire_attempt.side_effect = acquire
    provider.invoke.return_value = SelectorHopSuccess(
        text='{"lane":"economy"}',
        usage=ReportedSelectorUsage(prompt_tokens=100, completion_tokens=5, total_tokens=105),
    )
    service = admitted_selector_service(
        provider=provider,
        capacity_owner=capacity,
        capacity=SelectorCapacityBounds(
            DeploymentHealthRef(operation.attribution.deployment_id),
            rpm=100,
            tpm=10000,
            concurrency=2,
            token_allowance=1064,
        ),
        billing=billing,
        operation=operation,
        cache=ResponseCacheEligibility(cache),
    )
    kwargs = dict(
        state=RequestSelectorState(RequestDeadline.after(2)),
        payload=ChatCompletionRequest(
            model="group", messages=[{"role": "user", "content": "Hello"}]
        ),
        token_estimate=10,
        policy=policy,
        identity=identity,
    )
    return service, billing, capacity, provider, operation, kwargs


async def test_prerequisites_finalize_exact_receipt_once_before_returning_decision(
    selector_policy, policy_identity
):
    service, billing, capacity, provider, operation, kwargs = execution(
        selector_policy, policy_identity
    )
    first = await service.select_once(**kwargs)
    assert await service.select_once(**kwargs) is first
    billing.reserve.assert_awaited_once()
    billing.dispatch.assert_awaited_once()
    billing.accept_selector.assert_awaited_once()
    provider.invoke.assert_awaited_once()
    capacity.acquire_attempt.assert_awaited_once()
    capacity.release_attempt.assert_awaited_once()
    charge = billing.accept_selector.call_args.args[1]
    assert charge.attribution == operation.attribution
    assert charge.customer_charge == charge.provider_cost
    assert charge.usage.total_tokens == 105


@pytest.mark.parametrize("cache", [ResponseCacheOutcome.HIT, ResponseCacheOutcome.UNRESOLVED])
async def test_cached_or_unresolved_requests_cannot_do_any_hidden_work(
    selector_policy, policy_identity, cache
):
    service, billing, capacity, provider, _, kwargs = execution(
        selector_policy, policy_identity, cache=cache
    )
    with pytest.raises(RuntimeError, match="cache"):
        await service.select_once(**kwargs)
    billing.reserve.assert_not_awaited()
    capacity.acquire_attempt.assert_not_awaited()
    provider.invoke.assert_not_awaited()


async def test_recovered_dispatched_operation_never_replays_provider(
    selector_policy, policy_identity
):
    service, billing, capacity, provider, operation, kwargs = execution(
        selector_policy, policy_identity
    )
    billing.reserve.return_value = ReservedOperation(
        operation=operation,
        selector_state=ComponentState.PENDING,
        answer_state=ComponentState.RESERVED,
    )
    with pytest.raises(BillingOperationUnavailable):
        await service.select_once(**kwargs)
    capacity.acquire_attempt.assert_not_awaited()
    provider.invoke.assert_not_awaited()


async def test_cancellation_during_receipt_acceptance_retries_only_identical_receipt(
    selector_policy, policy_identity
):
    service, billing, capacity, provider, _, kwargs = execution(selector_policy, policy_identity)
    billing.accept_selector.side_effect = [asyncio.CancelledError(), None]
    with pytest.raises(asyncio.CancelledError):
        await service.select_once(**kwargs)
    assert billing.accept_selector.await_count == 2
    first, second = billing.accept_selector.call_args_list
    assert first == second
    provider.invoke.assert_awaited_once()
    capacity.release_attempt.assert_awaited_once()


async def test_unknown_usage_is_not_finalized_as_zero(selector_policy, policy_identity):
    service, billing, capacity, provider, _, kwargs = execution(selector_policy, policy_identity)
    provider.invoke.return_value = SelectorHopSuccess(
        text='{"lane":"quality"}', usage=UnknownSelectorUsage()
    )
    decision = await service.select_once(**kwargs)
    assert decision.usage.kind == "unknown"
    billing.dispatch.assert_awaited_once()
    billing.accept_selector.assert_not_awaited()
    billing.unattempted.assert_not_awaited()
    capacity.release_attempt.assert_awaited_once()


async def test_accounting_failure_never_becomes_a_safe_lane_default(
    selector_policy, policy_identity
):
    service, billing, _, provider, _, kwargs = execution(selector_policy, policy_identity)
    billing.reserve.side_effect = BillingOperationUnavailable()
    with pytest.raises(BillingOperationUnavailable):
        await service.select_once(**kwargs)
    provider.invoke.assert_not_awaited()


async def test_selector_deadline_during_reservation_never_defaults_without_admission(
    selector_policy, policy_identity
):
    service, billing, capacity, provider, _, kwargs = execution(selector_policy, policy_identity)
    kwargs["policy"] = kwargs["policy"].model_copy(update={"timeout_ms": 100})

    async def pending_reservation(*args, **kw):
        await asyncio.Event().wait()

    billing.reserve.side_effect = pending_reservation
    with pytest.raises(BillingOperationUnavailable):
        await service.select_once(**kwargs)
    capacity.acquire_attempt.assert_not_awaited()
    provider.invoke.assert_not_awaited()
    billing.unattempted.assert_not_awaited()
