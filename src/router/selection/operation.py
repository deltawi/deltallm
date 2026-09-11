from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import UUID, uuid4
from typing import Protocol

from src.billing.operation_reservation import SoftSelectorOperation
from src.billing.selector_charge import FrozenBillingContract, Identifier, SelectorChargeAttribution
from src.cache.execution_eligibility import ResponseCacheEligibility
from src.models.requests import ChatCompletionRequest
from src.router.selection.capacity import SelectorCapacityOwner
from src.router.selection.contracts import SelectorDecision, SelectorPolicyIdentity
from src.router.selection.qualification import QualifiedSelector
from src.router.selection.request_state import RequestSelectorState
from src.router.selection.runtime import SelectorExecutionFactory
from src.router.execution import RequestDeadline
from src.router.candidates import RouteCandidatePlanner
from src.router.group_execution import set_group_preparation
from src.router.selection.eligibility import (
    chat_requirements,
    clear_selector_capability_eligibility,
    set_selector_capability_eligibility,
)
from src.router.selection.contracts import SelectorInvariantError
from src.router.selection.request_state import SelectorState
from src.router.static_filters import required_request_tags, tags_allow_deployment
from src.router.selection.answer_observation import SelectorAnswerObservation

_OPERATION_KEY = "_deltallm_selector_operation"


class SelectorDecisionCheckpoint(Protocol):
    async def begin(self, group: str, identity: SelectorPolicyIdentity) -> None: ...

    async def finish(self, decision: SelectorDecision) -> None: ...


class SelectorPrincipal(FrozenBillingContract):
    api_key: Identifier
    user_id: Identifier | None = None
    team_id: Identifier | None = None
    organization_id: Identifier | None = None
    owner_account_id: Identifier | None = None


class SelectorOperation:
    """One application-level decision, prepared lazily by the execution owner."""

    def __init__(
        self,
        *,
        state: RequestSelectorState,
        selectors: Mapping[str, QualifiedSelector],
        factory: SelectorExecutionFactory,
        principal: SelectorPrincipal,
        operation_id: UUID,
        model: str,
        payload: ChatCompletionRequest,
        token_estimate: int,
        cache: ResponseCacheEligibility,
        capacity_owner: SelectorCapacityOwner,
        authorize: Callable[[str], None],
        observe: Callable[[SelectorDecision], None],
        run_connected: Callable[[Awaitable[SelectorDecision]], Awaitable[SelectorDecision]],
        context: dict[str, object],
        planner: RouteCandidatePlanner,
        checkpoint: SelectorDecisionCheckpoint | None = None,
    ) -> None:
        self.state, self._selectors, self._factory = state, selectors, factory
        self._principal, self._id, self._model = principal, operation_id, model
        self._payload, self._tokens, self._cache = payload, token_estimate, cache
        self._capacity, self._authorize, self._observe = capacity_owner, authorize, observe
        self._run_connected = run_connected
        self._context = context
        self._planner = planner
        self._checkpoint = checkpoint
        self.answer_observation = SelectorAnswerObservation(selectors)
        self._prepared_groups: set[str] = set()
        self.refresh_payload(payload, token_estimate)

    def refresh_payload(self, payload: ChatCompletionRequest, token_estimate: int) -> None:
        if self.state.state is SelectorState.RUNNING:
            raise SelectorInvariantError()
        self._payload, self._tokens = payload, token_estimate
        self._requirements = chat_requirements(payload)
        self._prepared_groups.clear()
        clear_selector_capability_eligibility(self._context)

    @property
    def deadline(self) -> RequestDeadline:
        return self.state.deadline

    async def prepare(self, model_group: str) -> None:
        qualified = self._selectors.get(model_group)
        if qualified is None:
            return
        self._authorize(model_group)
        if model_group not in self._prepared_groups:
            set_selector_capability_eligibility(
                self._context,
                self._payload,
                qualified.capabilities,
                requirements=self._requirements,
            )
            self._prepared_groups.add(model_group)
        previous = self.state.decision_for_planning()
        if previous is not None:
            return
        plans = await self.deadline.wait_for(
            self._planner.plan_deployments([model_group], self._context)
        )
        plan = plans.get(model_group)
        if plan is None or not any(lane.deployments for lane in plan.lanes):
            # Hard-rejected groups do not justify a paid classification. Execution
            # may proceed to a configured, eligible context fallback instead.
            return
        operation = SoftSelectorOperation(
            attribution=SelectorChargeAttribution(
                operation_id=self._id,
                **self._principal.model_dump(),
                model_group=self._model,
                deployment_id=qualified.target.deployment_id,
                deployment_model=qualified.deployment_model,
                provider=qualified.provider,
            ),
            owner_token=uuid4(),
            pricing=qualified.pricing,
            admission_allowance=qualified.admission_allowance,
            expires_at=datetime.now(UTC)
            + timedelta(seconds=min(840, self.state.deadline.require_remaining())),
        )
        service = self._factory.build(
            qualified,
            operation=operation,
            cache=self._cache,
            capacity_owner=self._capacity,
            classifier_allowed=tags_allow_deployment(
                qualified.classifier_tags, required_request_tags(self._context.get("metadata"))
            ),
            # A rejected financial admission must remain retryable. Once admitted,
            # persist the replay fence before any potentially paid dispatch.
            after_admission=(
                partial(self._checkpoint.begin, model_group, qualified.identity)
                if self._checkpoint is not None
                else None
            ),
        )
        decision = await self._run_connected(
            service.select_once(
                state=self.state,
                payload=self._payload,
                token_estimate=self._tokens,
                policy=qualified.routing.policy,
                identity=qualified.identity,
            )
        )
        if self._checkpoint is not None:
            await self._checkpoint.finish(decision)
        self.answer_observation.decision = decision
        self._observe(decision)


def bind_selector_preparation(context: dict[str, object], operation: SelectorOperation) -> None:
    if _OPERATION_KEY in context and context[_OPERATION_KEY] is not operation:
        raise SelectorInvariantError()
    context[_OPERATION_KEY] = operation
    set_group_preparation(context, operation)


def selector_answer_observation(context: dict[str, object]) -> SelectorAnswerObservation | None:
    operation = context.get(_OPERATION_KEY)
    if operation is None:
        return None
    if not isinstance(operation, SelectorOperation):
        raise SelectorInvariantError()
    return operation.answer_observation


def refresh_selector_payload(
    context: dict[str, object], payload: ChatCompletionRequest, token_estimate: int
) -> None:
    operation = context.get(_OPERATION_KEY)
    if operation is not None:
        if not isinstance(operation, SelectorOperation):
            raise SelectorInvariantError()
        operation.refresh_payload(payload, token_estimate)
