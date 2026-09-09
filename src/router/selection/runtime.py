from __future__ import annotations

import httpx
from collections.abc import Callable

from src.billing.operation_reservation import (
    BillingOperationUnavailable,
    OperationReservationStore,
    SoftSelectorOperation,
)
from src.cache.execution_eligibility import ResponseCacheEligibility
from src.providers.chat_upstream import ChatAdapterLookup
from src.router.selection.capacity import CapacityAdmittedSelectorHop, SelectorCapacityOwner
from src.router.selection.economics import AccountedSelectorHop, ReservedSelectorAdmission
from src.router.selection.provider import SelectorProviderHop
from src.router.selection.qualification import QualifiedSelector
from src.router.selection.service import SelectorService
from src.router.selection.contracts import (
    SelectorModelHop,
    SelectorHopFailure,
    SelectorCause,
    SelectorPrompt,
    UnattemptedSelectorUsage,
    SelectorHopOutcome,
)
from src.rate_limit_policy import estimate_tokens


class SelectorExecutionFactory:
    """Bootstrap-owned dependencies; every operation uses its pinned capacity owner."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        adapters: ChatAdapterLookup,
        billing: OperationReservationStore,
        default_openai_base_url: str,
        accounting_ready: Callable[[], bool],
    ) -> None:
        self._client, self._adapters = client, adapters
        self._billing, self._default_base = billing, default_openai_base_url
        self._accounting_ready = accounting_ready

    def build(
        self,
        qualified: QualifiedSelector,
        *,
        operation: SoftSelectorOperation,
        cache: ResponseCacheEligibility,
        capacity_owner: SelectorCapacityOwner,
        classifier_allowed: bool = True,
    ) -> SelectorService:
        self.require_ready()
        provider = SelectorProviderHop(
            client=self._client,
            adapters=self._adapters,
            target=qualified.target,
            default_openai_base_url=self._default_base,
        )
        hop: SelectorModelHop = CapacityAdmittedSelectorHop(
            owner=capacity_owner,
            bounds=qualified.capacity,
            hop=AccountedSelectorHop(store=self._billing, operation=operation, hop=provider),
        )
        if not classifier_allowed:
            hop = _PolicyRejectedHop()
        hop = _ContextCheckedHop(hop, input_capacity=qualified.capacity.token_allowance - 64)
        return SelectorService(
            hop,
            admission=ReservedSelectorAdmission(
                store=self._billing, operation=operation, cache=cache
            ),
        )

    def require_ready(self) -> None:
        if not self._accounting_ready():
            raise BillingOperationUnavailable()


class _PolicyRejectedHop:
    async def invoke(
        self, *, deployment_id: str, prompt: SelectorPrompt, expires_at: float
    ) -> SelectorHopFailure:
        return SelectorHopFailure(
            cause=SelectorCause.POLICY_DENIED, usage=UnattemptedSelectorUsage()
        )


class _ContextCheckedHop:
    def __init__(self, hop: SelectorModelHop, *, input_capacity: int) -> None:
        self._hop, self._input_capacity = hop, input_capacity

    async def invoke(
        self, *, deployment_id: str, prompt: SelectorPrompt, expires_at: float
    ) -> SelectorHopOutcome:
        tokens = estimate_tokens(
            {
                "messages": [
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.user},
                ]
            }
        )
        # The canonical request estimator plus output and framing margin, before
        # acquiring capacity or dispatching. Actual provider usage may differ.
        if tokens + 64 + 256 > self._input_capacity:
            return SelectorHopFailure(
                cause=SelectorCause.INPUT_BUDGET_INSUFFICIENT, usage=UnattemptedSelectorUsage()
            )
        return await self._hop.invoke(
            deployment_id=deployment_id, prompt=prompt, expires_at=expires_at
        )
