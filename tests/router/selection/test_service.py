import asyncio
import weakref
from unittest.mock import AsyncMock

import pytest

from src.models.errors import AuthenticationError, BudgetExceededError
from src.models.errors import TimeoutError as RequestTimeoutError
from src.models.requests import ChatCompletionRequest
from src.router.execution import RequestDeadline
from src.router.selection.contracts import (
    ReportedSelectorUsage,
    SelectorCause,
    SelectorHopFailure,
    SelectorHopSuccess,
    SelectorOperationAbortedError,
    UnknownSelectorUsage,
)
from src.router.selection.request_state import RequestSelectorState, SelectorState
from src.router.selection.service import SelectorService

USAGE = ReportedSelectorUsage(prompt_tokens=13, completion_tokens=5, total_tokens=18)


def state(seconds=2):
    return RequestSelectorState(RequestDeadline(asyncio.get_running_loop().time() + seconds))


def payload(text="Summarize this text"):
    return ChatCompletionRequest(model="public-group", messages=[{"role": "user", "content": text}])


def hop(text='{"lane":"economy"}'):
    return AsyncMock(invoke=AsyncMock(return_value=SelectorHopSuccess(text=text, usage=USAGE)))


async def select(service, operation, selector_policy, policy_identity, request=None):
    return await service.select_once(
        state=operation,
        payload=request or payload(),
        token_estimate=42,
        policy=selector_policy,
        identity=policy_identity,
    )


@pytest.mark.asyncio
async def test_classified_immutable_result_reused_across_payload_and_policy_changes(
    selector_policy, policy_identity
):
    provider = hop()
    service, operation = SelectorService(provider), state()
    result = await select(service, operation, selector_policy, policy_identity)
    changed = selector_policy.model_copy(update={"classifier_deployment_id": "different"})
    again = await select(service, operation, changed, policy_identity, payload("different request"))
    assert again is result
    assert result.lane == "economy" and result.minimum_rank == 0 and not result.used_default
    assert result.policy_identity is policy_identity and result.usage is USAGE
    assert result.latency_ms >= 0 and operation.state is SelectorState.DECIDED
    provider.invoke.assert_awaited_once()
    assert provider.invoke.call_args.kwargs["deployment_id"] == "classifier-concrete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,cause",
    [("garbage", SelectorCause.INVALID_JSON), ('{"lane":"invented"}', SelectorCause.UNKNOWN_LANE)],
)
async def test_invalid_output_defaults_and_preserves_reported_usage(
    selector_policy, policy_identity, text, cause
):
    operation = state()
    result = await select(SelectorService(hop(text)), operation, selector_policy, policy_identity)
    assert (result.lane, result.minimum_rank, result.cause) == ("quality", 1, cause)
    assert result.used_default and result.usage is USAGE


@pytest.mark.asyncio
async def test_provider_failure_is_not_a_retry(selector_policy, policy_identity):
    provider = hop()
    provider.invoke.return_value = SelectorHopFailure(
        cause=SelectorCause.PROVIDER_ERROR, usage=UnknownSelectorUsage()
    )
    result = await select(SelectorService(provider), state(), selector_policy, policy_identity)
    assert result.cause is SelectorCause.PROVIDER_ERROR and result.usage.kind == "unknown"
    provider.invoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "budget", "unicode"])
async def test_defaults_without_a_provider_call(selector_policy, policy_identity, mode):
    provider, request = hop(), payload()
    cause = SelectorCause.INPUT_UNAVAILABLE
    if mode == "missing":
        request = ChatCompletionRequest(
            model="group", messages=[{"role": "assistant", "content": "not a user"}]
        )
    elif mode == "budget":
        selector_policy = selector_policy.model_copy(update={"max_input_chars": 256})
        cause = SelectorCause.INPUT_BUDGET_INSUFFICIENT
    else:
        request = payload("\ud800")
        cause = SelectorCause.INVALID_INPUT
    result = await select(
        SelectorService(provider), state(), selector_policy, policy_identity, request
    )
    assert result.cause is cause and result.usage.kind == "not_attempted"
    provider.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_deadline_defaults_only_when_parent_still_live(
    selector_policy, policy_identity
):
    closed = asyncio.Event()

    async def delayed(**kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    provider = hop()
    provider.invoke.side_effect = delayed
    operation = state()
    policy = selector_policy.model_copy(update={"timeout_ms": 100})
    result = await select(SelectorService(provider), operation, policy, policy_identity)
    assert result.cause is SelectorCause.SELECTOR_TIMEOUT and closed.is_set()
    assert operation.state is SelectorState.DECIDED and operation.usage.kind == "unknown"
    assert 0 < provider.invoke.call_args.kwargs["expires_at"] < operation.deadline.expires_at


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [-1, 0.015])
async def test_parent_deadline_aborts_and_never_restarts(selector_policy, policy_identity, seconds):
    provider, operation = hop(), state(seconds)

    async def delayed(**kwargs):
        await asyncio.Event().wait()

    provider.invoke.side_effect = delayed
    service = SelectorService(provider)
    with pytest.raises(RequestTimeoutError):
        await select(service, operation, selector_policy, policy_identity)
    with pytest.raises(RequestTimeoutError):
        await select(service, operation, selector_policy, policy_identity)
    assert operation.state is SelectorState.ABORTED
    assert provider.invoke.await_count == (0 if seconds < 0 else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [AuthenticationError(), BudgetExceededError(), RuntimeError("private detail")]
)
async def test_non_provider_failures_propagate_without_reclassification(
    selector_policy, policy_identity, error
):
    provider, operation = hop(), state()
    provider.invoke.side_effect = error
    service = SelectorService(provider)
    with pytest.raises(type(error)) as caught:
        await select(service, operation, selector_policy, policy_identity)
    assert caught.value is error
    with pytest.raises(SelectorOperationAbortedError) as repeated:
        await select(service, operation, selector_policy, policy_identity)
    assert "private" not in str(repeated.value)
    assert not operation._done.exception()
    provider.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_operation_has_no_global_memoization(selector_policy, policy_identity):
    provider = hop()
    service = SelectorService(provider)
    first = await select(service, state(), selector_policy, policy_identity)
    second = await select(service, state(), selector_policy, policy_identity)
    assert first is not second and provider.invoke.await_count == 2


@pytest.mark.asyncio
async def test_completed_state_does_not_retain_the_source_request(selector_policy, policy_identity):
    request = payload("private source")
    reference = weakref.ref(request)
    operation = state()
    result = await select(
        SelectorService(hop()), operation, selector_policy, policy_identity, request
    )
    del request
    assert reference() is None and operation._done.result() is None
    assert "private" not in repr(result) and not hasattr(operation, "__dict__")


@pytest.mark.asyncio
async def test_returning_default_lane_can_be_a_successful_classification(
    selector_policy, policy_identity
):
    result = await select(
        SelectorService(hop('{"lane":"quality"}')), state(), selector_policy, policy_identity
    )
    assert (
        result.cause is SelectorCause.CLASSIFIED
        and not result.used_default
        and result.minimum_rank == 1
    )


@pytest.mark.asyncio
async def test_late_synchronous_hop_completion_cannot_outrun_parent_expiry(
    selector_policy, policy_identity
):
    operation, provider = state(), hop()

    async def synchronous_completion(**kwargs):
        # Model an expiry during bounded synchronous translation without wall-clock sleeps.
        object.__setattr__(operation.deadline, "expires_at", asyncio.get_running_loop().time() - 1)
        return SelectorHopSuccess(text='{"lane":"economy"}', usage=USAGE)

    provider.invoke.side_effect = synchronous_completion
    with pytest.raises(RequestTimeoutError):
        await select(SelectorService(provider), operation, selector_policy, policy_identity)
    assert operation.state is SelectorState.ABORTED and operation.usage is USAGE
