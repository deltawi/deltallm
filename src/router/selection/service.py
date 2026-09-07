from __future__ import annotations

import asyncio

from src.models.requests import ChatCompletionRequest
from src.route_policy_contract import LLMTierSelectorPolicy, SelectorLane
from src.router.selection.contracts import (
    SelectorCause,
    SelectorDecision,
    SelectorHopFailure,
    SelectorModelHop,
    SelectorPolicyIdentity,
    SelectorUsage,
    UnknownSelectorUsage,
)
from src.router.selection.parser import parse_selector_output
from src.router.selection.prompt import build_selector_prompt, project_selector_request
from src.router.selection.request_state import RequestSelectorState


class SelectorService:
    """Isolated prerequisite: deliberately not constructed by production bootstrap."""

    def __init__(self, hop: SelectorModelHop) -> None:
        self._hop = hop

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
        loop = asyncio.get_running_loop()
        started = loop.time()
        expires_at = min(state.deadline.expires_at, started + policy.timeout_ms / 1000)
        result: SelectorLane | SelectorCause
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
