from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from src.billing.operation_reservation import BillingOperationUnavailable
from src.metrics.selector import (
    SelectorTermination,
    observe_selector_decision,
    observe_selector_termination,
)
from src.models.errors import TimeoutError as RequestTimeoutError

from src.models.requests import ChatCompletionRequest
from src.route_policy_contract import LLMTierSelectorPolicy, SelectorLane
from src.router.selection.contracts import (
    SelectorCause,
    SelectorDecision,
    SelectorHopFailure,
    SelectorModelHop,
    SelectorPolicyIdentity,
    SelectorUsage,
    SelectorAdmission,
    SelectorInvariantError,
    UnknownSelectorUsage,
)
from src.router.selection.parser import parse_selector_output
from src.router.selection.prompt import build_selector_prompt, project_selector_request
from src.router.selection.request_state import RequestSelectorState


class SelectorService:
    """One bounded decision composed by the authenticated execution owner."""

    def __init__(
        self,
        hop: SelectorModelHop,
        *,
        admission: SelectorAdmission | None = None,
        after_admission: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._hop = hop
        self._admission = admission
        self._after_admission = after_admission

    async def select_once(
        self,
        *,
        state: RequestSelectorState,
        payload: ChatCompletionRequest,
        token_estimate: int,
        policy: LLMTierSelectorPolicy,
        identity: SelectorPolicyIdentity,
    ) -> SelectorDecision:
        return await state.select_once(
            lambda: self._decide(
                state=state,
                payload=payload,
                token_estimate=token_estimate,
                policy=policy,
                identity=identity,
            )
        )

    async def _decide(
        self,
        *,
        state: RequestSelectorState,
        payload: ChatCompletionRequest,
        token_estimate: int,
        policy: LLMTierSelectorPolicy,
        identity: SelectorPolicyIdentity,
    ) -> SelectorDecision:
        started = asyncio.get_running_loop().time()
        try:
            decision = await self._execute(
                state=state,
                payload=payload,
                token_estimate=token_estimate,
                policy=policy,
                identity=identity,
            )
        except asyncio.CancelledError:
            observe_selector_termination(
                SelectorTermination.CANCELLED, seconds=asyncio.get_running_loop().time() - started
            )
            raise
        except RequestTimeoutError:
            observe_selector_termination(
                SelectorTermination.DEADLINE, seconds=asyncio.get_running_loop().time() - started
            )
            raise
        except BillingOperationUnavailable:
            observe_selector_termination(
                SelectorTermination.ACCOUNTING_UNAVAILABLE,
                seconds=asyncio.get_running_loop().time() - started,
            )
            raise
        except SelectorInvariantError:
            observe_selector_termination(
                SelectorTermination.INVARIANT_FAILURE,
                seconds=asyncio.get_running_loop().time() - started,
            )
            raise
        observe_selector_decision(decision)
        return decision

    async def _execute(
        self,
        *,
        state: RequestSelectorState,
        payload: ChatCompletionRequest,
        token_estimate: int,
        policy: LLMTierSelectorPolicy,
        identity: SelectorPolicyIdentity,
    ) -> SelectorDecision:
        loop = asyncio.get_running_loop()
        started = loop.time()
        expires_at = min(state.deadline.expires_at, started + policy.timeout_ms / 1000)
        result: SelectorLane | SelectorCause
        if self._admission is not None:
            try:
                async with asyncio.timeout_at(expires_at):
                    await self._admission.admit(expires_at=expires_at)
                    if self._after_admission is not None:
                        await self._after_admission()
            except TimeoutError:
                # An ambiguous reservation is not permission to answer in a default
                # lane. Only provider/selection timeouts may default after admission.
                state.deadline.require_remaining()
                raise BillingOperationUnavailable() from None
        try:
            async with asyncio.timeout_at(expires_at):
                features = project_selector_request(payload, token_estimate=token_estimate)
                prompt = (
                    features
                    if isinstance(features, SelectorCause)
                    else build_selector_prompt(features, policy)
                )
                if isinstance(prompt, SelectorCause):
                    result = prompt
                else:
                    if loop.time() >= expires_at:
                        raise TimeoutError()
                    state.observe_usage(UnknownSelectorUsage())
                    outcome = await self._hop.invoke(
                        deployment_id=policy.classifier_deployment_id,
                        prompt=prompt,
                        expires_at=expires_at,
                    )
                    state.observe_usage(outcome.usage)
                    result = (
                        outcome.cause
                        if isinstance(outcome, SelectorHopFailure)
                        else parse_selector_output(outcome.text, policy)
                    )
                if loop.time() >= expires_at:
                    raise TimeoutError()
        except TimeoutError:
            state.deadline.require_remaining()
            result = SelectorCause.SELECTOR_TIMEOUT
        state.deadline.require_remaining()
        if self._admission is not None:
            await self._admission.finish(state.usage, expires_at=state.deadline.expires_at)
        return _decision(
            result,
            policy=policy,
            identity=identity,
            usage=state.usage,
            latency_ms=(loop.time() - started) * 1000,
        )


def _decision(
    result: SelectorLane | SelectorCause,
    *,
    policy: LLMTierSelectorPolicy,
    identity: SelectorPolicyIdentity,
    usage: SelectorUsage,
    latency_ms: float,
) -> SelectorDecision:
    if isinstance(result, SelectorLane):
        lane, cause = result, SelectorCause.CLASSIFIED
    else:
        lane = next(lane for lane in policy.lanes if lane.id == policy.default_lane)
        cause = result
    return SelectorDecision(
        lane=lane.id,
        minimum_rank=lane.rank,
        cause=cause,
        latency_ms=latency_ms,
        policy_identity=identity,
        usage=usage,
    )
